"""Dispatch source builds to a GitLab CI/CD project pipeline."""

from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.models import BuildRun


@dataclass(frozen=True)
class GitLabPipelineConfiguration:
    token: str
    api_url: str
    server_url: str
    callback_url: str
    timeout_seconds: float = 20.0


@dataclass(frozen=True)
class GitLabPipelineDispatchResult:
    external_run_id: str
    run_url: str


class GitLabPipelineError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def parse_gitlab_repository(repository_url: str, server_url: str) -> str:
    """Return a validated nested GitLab project path, without a trailing .git."""
    parsed = urlsplit(repository_url)
    configured = urlsplit(server_url)
    try:
        same_port = parsed.port == configured.port
    except ValueError:
        same_port = False
    if (
        parsed.scheme != "https"
        or parsed.hostname != configured.hostname
        or not same_port
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise GitLabPipelineError(
            "unsupported_repository_provider",
            "Repository URL must use the configured GitLab server.",
        )
    if configured.path not in {"", "/"}:
        raise GitLabPipelineError(
            "invalid_gitlab_server_url",
            "GitLab server URL must be an origin without a path.",
        )
    path = unquote(parsed.path).strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) < 2 or any(
        not part
        or part in {".", ".."}
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
            for character in part
        )
        for part in parts
    ):
        raise GitLabPipelineError(
            "invalid_repository_url",
            "Repository URL must identify a GitLab namespace and project.",
        )
    return "/".join(parts)


def dispatch_pipeline(
    build_run: BuildRun,
    configuration: GitLabPipelineConfiguration,
    *,
    transport: httpx.BaseTransport | None = None,
) -> GitLabPipelineDispatchResult:
    project_path = parse_gitlab_repository(build_run.repository_url or "", configuration.server_url)
    source_ref = build_run.source_ref
    if source_ref.startswith("refs/heads/"):
        source_ref = source_ref.removeprefix("refs/heads/")
    elif source_ref.startswith("refs/tags/"):
        source_ref = source_ref.removeprefix("refs/tags/")
    elif source_ref.startswith("refs/") or not source_ref:
        raise GitLabPipelineError(
            "invalid_source_ref", "GitLab pipeline refs must be branch or tag names."
        )

    endpoint = (
        f"{configuration.api_url.rstrip('/')}/projects/{quote(project_path, safe='')}/pipeline"
    )
    callback_url = (
        f"{configuration.callback_url.rstrip('/')}/api/v1/build-runs/{build_run.id}/callback"
    )
    body = {
        "ref": source_ref,
        "variables": [
            {"key": "DEPLOYFORGE_BUILD_RUN_ID", "value": str(build_run.id)},
            {"key": "DEPLOYFORGE_DEPLOYMENT_ID", "value": str(build_run.deployment_id)},
            {"key": "DEPLOYFORGE_CALLBACK_URL", "value": callback_url},
        ],
    }
    try:
        with httpx.Client(
            timeout=configuration.timeout_seconds,
            transport=transport,
            follow_redirects=False,
        ) as client:
            response = client.post(
                endpoint,
                headers={
                    "Accept": "application/json",
                    "PRIVATE-TOKEN": configuration.token,
                    "User-Agent": "DeployForge-Control-Plane",
                },
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise GitLabPipelineError(
            "gitlab_pipeline_timeout",
            "GitLab did not respond before the pipeline dispatch timeout.",
            retryable=True,
        ) from exc
    except httpx.HTTPError as exc:
        raise GitLabPipelineError(
            "gitlab_pipeline_unavailable",
            "GitLab could not be reached for pipeline dispatch.",
            retryable=True,
        ) from exc

    if response.status_code == 429 or response.status_code >= 500:
        raise GitLabPipelineError(
            "gitlab_pipeline_temporarily_unavailable",
            "GitLab temporarily rejected the pipeline dispatch.",
            retryable=True,
        )
    if response.status_code in {401, 403}:
        raise GitLabPipelineError(
            "gitlab_pipeline_permission_denied",
            "GitLab rejected the configured credential or its project permissions.",
        )
    if response.status_code in {400, 404, 409, 422}:
        raise GitLabPipelineError(
            "gitlab_pipeline_unavailable",
            "GitLab rejected the ref or pipeline configuration for this project.",
        )
    if response.status_code != 201:
        raise GitLabPipelineError(
            "gitlab_pipeline_rejected",
            f"GitLab rejected pipeline dispatch with HTTP {response.status_code}.",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise GitLabPipelineError(
            "gitlab_pipeline_invalid_response",
            "GitLab returned invalid pipeline JSON.",
            retryable=True,
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("id"), (str, int)):
        raise GitLabPipelineError(
            "gitlab_pipeline_invalid_response",
            "GitLab returned a pipeline without a valid ID.",
            retryable=True,
        )
    run_url = payload.get("web_url")
    if not isinstance(run_url, str):
        raise GitLabPipelineError(
            "gitlab_pipeline_invalid_response",
            "GitLab returned a pipeline without a web URL.",
            retryable=True,
        )
    parsed_run_url = urlsplit(run_url)
    configured = urlsplit(configuration.server_url)
    try:
        same_port = parsed_run_url.port == configured.port
    except ValueError:
        same_port = False
    if (
        parsed_run_url.scheme != "https"
        or parsed_run_url.hostname != configured.hostname
        or not same_port
        or parsed_run_url.username
        or parsed_run_url.password
        or parsed_run_url.query
        or parsed_run_url.fragment
    ):
        raise GitLabPipelineError(
            "gitlab_pipeline_invalid_response",
            "GitLab returned a pipeline URL outside the configured server.",
        )
    return GitLabPipelineDispatchResult(str(payload["id"]), run_url)
