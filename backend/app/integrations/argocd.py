"""Small, credential-safe client for the Argo CD Applications API."""

from dataclasses import dataclass
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class ArgoCDConfiguration:
    api_url: str
    token: str
    timeout_seconds: int = 15


class ArgoCDError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ArgoCDClient:
    def __init__(
        self,
        configuration: ArgoCDConfiguration,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.configuration = configuration
        self.transport = transport

    def _request(self, method: str, path: str, *, body: dict | None = None) -> dict:
        endpoint = f"{self.configuration.api_url.rstrip('/')}/api/v1{path}"
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
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self.configuration.token}",
                        "Content-Type": "application/json",
                        "User-Agent": "DeployForge-Control-Plane",
                    },
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise ArgoCDError(
                "argocd_request_timeout",
                "Argo CD did not respond before the request timeout.",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ArgoCDError(
                "argocd_unavailable", "Argo CD could not be reached.", retryable=True
            ) from exc

        if response.status_code in {200, 201, 202}:
            try:
                payload = response.json()
            except ValueError as exc:
                raise ArgoCDError(
                    "argocd_invalid_response",
                    "Argo CD returned an invalid JSON response.",
                    retryable=True,
                ) from exc
            if not isinstance(payload, dict):
                raise ArgoCDError(
                    "argocd_invalid_response",
                    "Argo CD returned an unexpected response shape.",
                    retryable=True,
                )
            return payload
        if response.status_code == 404:
            raise ArgoCDError("argocd_application_not_found", "Argo CD application was not found.")
        if response.status_code in {401, 403}:
            raise ArgoCDError(
                "argocd_permission_denied",
                "Argo CD rejected the configured credential or permissions.",
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ArgoCDError(
                "argocd_temporarily_unavailable",
                "Argo CD temporarily rejected the request.",
                retryable=True,
            )
        raise ArgoCDError(
            "argocd_request_rejected",
            f"Argo CD rejected the request with HTTP {response.status_code}.",
        )

    def get_application(self, application_name: str) -> dict:
        encoded_name = quote(application_name, safe="")
        return self._request("GET", f"/applications/{encoded_name}")

    def sync_application(self, application_name: str, revision: str) -> dict:
        """Sync the exact immutable GitOps revision selected by DeployForge."""
        encoded_name = quote(application_name, safe="")
        return self._request(
            "POST",
            f"/applications/{encoded_name}/sync",
            body={"revision": revision, "prune": False, "dryRun": False},
        )
