"""GitHub REST client for generating repositories from platform-owned templates."""

import base64
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from nacl.public import PublicKey, SealedBox


@dataclass(frozen=True)
class GitHubRepositoryConfiguration:
    token: str
    api_url: str
    timeout_seconds: float = 20.0


class GitHubRepositoryError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class GitHubRepositoryClient:
    def __init__(
        self,
        configuration: GitHubRepositoryConfiguration,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.configuration = configuration
        self.transport = transport

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        generation_request: bool = False,
    ) -> dict | None:
        endpoint = f"{self.configuration.api_url.rstrip('/')}{path}"
        try:
            with httpx.Client(
                timeout=self.configuration.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.request(
                    method,
                    endpoint,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {self.configuration.token}",
                        "X-GitHub-Api-Version": "2022-11-28",
                        "User-Agent": "DeployForge-Control-Plane",
                    },
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise GitHubRepositoryError(
                "github_repository_timeout",
                "GitHub did not respond before the repository request timeout.",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise GitHubRepositoryError(
                "github_repository_unavailable",
                "GitHub could not be reached for repository provisioning.",
                retryable=True,
            ) from exc

        if response.status_code == 404 and method == "GET":
            return None
        if response.status_code in {200, 201}:
            try:
                payload = response.json()
            except ValueError as exc:
                raise GitHubRepositoryError(
                    "github_repository_invalid_response",
                    "GitHub returned invalid repository JSON.",
                    retryable=True,
                ) from exc
            if not isinstance(payload, dict):
                raise GitHubRepositoryError(
                    "github_repository_invalid_response",
                    "GitHub returned an unexpected repository response.",
                    retryable=True,
                )
            return payload
        if response.status_code == 204:
            return {}
        if response.status_code == 403 and (
            response.headers.get("x-ratelimit-remaining") == "0"
            or response.headers.get("retry-after")
        ):
            raise GitHubRepositoryError(
                "github_repository_rate_limited",
                "GitHub rate limited the repository provisioning request.",
                retryable=True,
            )
        if response.status_code in {401, 403}:
            raise GitHubRepositoryError(
                "github_repository_permission_denied",
                "GitHub rejected the repository provisioning credential or its permissions.",
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise GitHubRepositoryError(
                "github_repository_temporarily_unavailable",
                "GitHub temporarily rejected the repository provisioning request.",
                retryable=True,
            )
        if response.status_code == 404 and path.endswith("/generate"):
            raise GitHubRepositoryError(
                "github_repository_template_not_found",
                "The configured GitHub repository template was not found or is inaccessible.",
            )
        if response.status_code == 404:
            raise GitHubRepositoryError(
                "github_repository_resource_not_found",
                "GitHub could not find the destination repository or Actions resource.",
            )
        if response.status_code == 422:
            if generation_request:
                raise GitHubRepositoryError(
                    "github_repository_generation_conflict",
                    "GitHub could not generate the repository; check its name and "
                    "organization policy.",
                )
            raise GitHubRepositoryError(
                "github_repository_request_rejected",
                "GitHub rejected the repository bootstrap settings.",
            )
        raise GitHubRepositoryError(
            "github_repository_request_rejected",
            f"GitHub rejected repository provisioning with HTTP {response.status_code}.",
        )

    @staticmethod
    def _repository_path(owner: str, repository: str) -> str:
        return f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"

    def get_repository(self, owner: str, repository: str) -> dict | None:
        return self._request("GET", self._repository_path(owner, repository))

    def generate_from_template(
        self,
        *,
        template_owner: str,
        template_name: str,
        owner: str,
        name: str,
        description: str,
        private: bool,
    ) -> dict:
        endpoint = (
            f"/repos/{quote(template_owner, safe='')}/{quote(template_name, safe='')}/generate"
        )
        response = self._request(
            "POST",
            endpoint,
            generation_request=True,
            body={
                "owner": owner,
                "name": name,
                "description": description,
                "include_all_branches": False,
                "private": private,
            },
        )
        assert response is not None
        return response

    def configure_actions_bootstrap(
        self,
        owner: str,
        repository: str,
        *,
        callback_secret: str,
        callback_allowed_host: str,
    ) -> None:
        base_path = self._repository_path(owner, repository) + "/actions"
        public_key = self._request("GET", f"{base_path}/secrets/public-key")
        if not isinstance(public_key, dict):
            raise GitHubRepositoryError(
                "github_repository_public_key_missing",
                "GitHub did not return an Actions secret encryption key.",
            )
        key_value = public_key.get("key")
        key_id = public_key.get("key_id")
        if not isinstance(key_value, str) or not isinstance(key_id, str):
            raise GitHubRepositoryError(
                "github_repository_public_key_invalid",
                "GitHub returned an invalid Actions secret encryption key.",
                retryable=True,
            )
        try:
            key_bytes = base64.b64decode(key_value, validate=True)
            sealed_secret = SealedBox(PublicKey(key_bytes)).encrypt(callback_secret.encode())
        except (ValueError, TypeError) as exc:
            raise GitHubRepositoryError(
                "github_repository_public_key_invalid",
                "GitHub returned an invalid Actions secret encryption key.",
            ) from exc
        encrypted_value = base64.b64encode(sealed_secret).decode("ascii")
        self._request(
            "PUT",
            f"{base_path}/secrets/DEPLOYFORGE_CALLBACK_SECRET",
            body={"encrypted_value": encrypted_value, "key_id": key_id},
        )

        variables_path = f"{base_path}/variables"
        variable_name = "DEPLOYFORGE_CALLBACK_ALLOWED_HOST"
        variable_path = f"{variables_path}/{variable_name}"
        existing = self._request("GET", variable_path)
        if existing is None:
            self._request(
                "POST",
                variables_path,
                body={"name": variable_name, "value": callback_allowed_host},
            )
        else:
            self._request(
                "PATCH",
                variable_path,
                body={"name": variable_name, "value": callback_allowed_host},
            )
