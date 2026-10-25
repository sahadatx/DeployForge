"""Dispatch CI work to a repository-owned GitHub Actions workflow."""

from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import httpx

from app.models import BuildRun

GITHUB_API_VERSION = "2022-11-28"


@dataclass(frozen=True)
class DispatchConfiguration:
    token: str
    api_url: str
    callback_url: str


class GitHubDispatchError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def parse_github_repository(repository_url: str) -> tuple[str, str]:
    parsed = urlsplit(repository_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise GitHubDispatchError(
            "unsupported_repository_provider",
            "CI dispatch currently supports canonical github.com repository URLs.",
        )
    path = parsed.path.strip("/")
    parts = path.split("/")
    if len(parts) != 2 or any(not part for part in parts):
        raise GitHubDispatchError(
            "invalid_repository_url",
            "Repository URL must identify one GitHub owner and repository.",
        )
    owner, repository = parts
    if repository.endswith(".git"):
        repository = repository[:-4]
    valid_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
    if not owner or not repository or set(owner + repository) - valid_chars:
        raise GitHubDispatchError(
            "invalid_repository_url", "Repository URL contains unsupported path characters."
        )
    return owner, repository


def dispatch_workflow(
    build_run: BuildRun,
    configuration: DispatchConfiguration,
    *,
    workflow_path: str,
    transport: httpx.BaseTransport | None = None,
) -> None:
    callback_url = (
        f"{configuration.callback_url.rstrip('/')}/api/v1/build-runs/{build_run.id}/callback"
    )
    dispatch_workflow_inputs(
        build_run.repository_url,
        build_run.source_ref,
        configuration,
        workflow_path=workflow_path,
        inputs={
            "build_run_id": str(build_run.id),
            "deployment_id": str(build_run.deployment_id),
            "source_ref": build_run.source_ref,
            "callback_url": callback_url,
        },
        transport=transport,
    )


def dispatch_workflow_inputs(
    repository_url: str,
    source_ref: str,
    configuration: DispatchConfiguration,
    *,
    workflow_path: str,
    inputs: dict[str, str],
    transport: httpx.BaseTransport | None = None,
) -> None:
    owner, repository = parse_github_repository(repository_url)
    workflow_filename = workflow_path.rsplit("/", 1)[-1]
    encoded_path = quote(workflow_filename)
    endpoint = (
        f"{configuration.api_url.rstrip('/')}/repos/{quote(owner)}/{quote(repository)}"
        f"/actions/workflows/{encoded_path}/dispatches"
    )
    body = {"ref": source_ref, "inputs": inputs}
    try:
        with httpx.Client(timeout=15.0, transport=transport) as client:
            response = client.post(
                endpoint,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {configuration.token}",
                    "X-GitHub-Api-Version": GITHUB_API_VERSION,
                    "User-Agent": "DeployForge-Control-Plane",
                },
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise GitHubDispatchError(
            "github_dispatch_timeout",
            "GitHub did not respond before the dispatch timeout.",
            retryable=True,
        ) from exc
    except httpx.HTTPError as exc:
        raise GitHubDispatchError(
            "github_dispatch_unavailable",
            "GitHub could not be reached for workflow dispatch.",
            retryable=True,
        ) from exc

    if response.status_code == 204:
        return
    if response.status_code == 429 or response.status_code >= 500:
        raise GitHubDispatchError(
            "github_dispatch_temporarily_unavailable",
            "GitHub temporarily rejected the workflow dispatch.",
            retryable=True,
        )
    if response.status_code in {401, 403}:
        raise GitHubDispatchError(
            "github_dispatch_permission_denied",
            "GitHub rejected the configured credential or its repository permissions.",
        )
    if response.status_code in {404, 422}:
        raise GitHubDispatchError(
            "github_workflow_unavailable",
            "The configured workflow is missing, disabled, or does not accept workflow_dispatch.",
        )
    raise GitHubDispatchError(
        "github_dispatch_rejected",
        f"GitHub rejected the workflow dispatch with HTTP {response.status_code}.",
    )
