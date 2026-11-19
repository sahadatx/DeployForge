"""Dispatch source builds to explicitly mapped Azure DevOps YAML pipelines."""

import base64
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.models import BuildRun


@dataclass(frozen=True)
class AzurePipelineBinding:
    pipeline_id: int
    signer_identity: str
    config_path: str


@dataclass(frozen=True)
class AzurePipelineConfiguration:
    token: str
    organization_url: str
    oidc_issuer: str
    pipeline_bindings: dict[str, dict[str, object]]
    callback_url: str
    timeout_seconds: float = 20.0


@dataclass(frozen=True)
class AzurePipelineDispatchResult:
    external_run_id: str
    run_url: str


class AzurePipelineError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def azure_pipeline_reference(binding: AzurePipelineBinding) -> str:
    """Snapshot the selected pipeline and expected certificate identity on a build run."""
    return f"azure-pipeline:{binding.pipeline_id}:{binding.signer_identity}"


def parse_azure_pipeline_reference(value: str) -> tuple[int, str] | None:
    prefix, separator, remainder = value.partition(":")
    if prefix != "azure-pipeline" or not separator:
        return None
    pipeline_id_text, identity_separator, signer_identity = remainder.partition(":")
    if (
        not pipeline_id_text.isdigit()
        or not 1 <= int(pipeline_id_text) <= 2_147_483_647
        or not identity_separator
        or not signer_identity.startswith("https://")
    ):
        return None
    return int(pipeline_id_text), signer_identity


def parse_azure_repository(repository_url: str, organization_url: str) -> tuple[str, str]:
    """Return the configured Azure organization repository as (project, repo)."""
    parsed = urlsplit(repository_url)
    configured = urlsplit(organization_url)
    try:
        same_port = (parsed.port or 443) == (configured.port or 443)
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
        raise AzurePipelineError(
            "unsupported_repository_provider",
            "Repository URL must use the configured Azure DevOps organization.",
        )

    org_path = configured.path.strip("/")
    raw_parts = parsed.path.strip("/").split("/")
    if len(raw_parts) != 4 or unquote(raw_parts[0]).casefold() != unquote(org_path).casefold():
        raise AzurePipelineError(
            "invalid_repository_url",
            "Repository URL must use the configured organization/project/_git/repository format.",
        )
    parts = [unquote(part) for part in raw_parts]
    project, git_marker, repository = parts[1:]
    if (
        git_marker != "_git"
        or not project
        or project in {".", ".."}
        or not repository
        or repository in {".", ".."}
        or any(
            "/" in part or "\\" in part or any(ord(char) < 32 for char in part)
            for part in (project, repository)
        )
    ):
        raise AzurePipelineError(
            "invalid_repository_url",
            "Repository URL contains an unsupported Azure project or repository name.",
        )
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not repository:
        raise AzurePipelineError("invalid_repository_url", "Repository name cannot be blank.")
    return project, repository


def pipeline_binding_for_repository(
    repository_url: str,
    organization_url: str,
    pipeline_bindings: dict[str, dict[str, object]],
) -> AzurePipelineBinding:
    project, repository = parse_azure_repository(repository_url, organization_url)
    raw_binding = pipeline_bindings.get(f"{project}/{repository}".casefold())
    if not isinstance(raw_binding, dict):
        raise AzurePipelineError(
            "azure_pipeline_not_mapped",
            "No Azure DevOps pipeline is configured for this project and repository.",
        )
    try:
        pipeline_id = int(raw_binding["pipeline_id"])
        signer_identity = str(raw_binding["signer_identity"])
        config_path = str(raw_binding.get("config_path", "azure-pipelines.yml"))
    except (KeyError, TypeError, ValueError) as exc:
        raise AzurePipelineError(
            "azure_pipeline_not_mapped", "The Azure DevOps pipeline binding is invalid."
        ) from exc
    if pipeline_id < 1 or not signer_identity or not config_path:
        raise AzurePipelineError(
            "azure_pipeline_not_mapped", "The Azure DevOps pipeline binding is invalid."
        )
    return AzurePipelineBinding(pipeline_id, signer_identity, config_path)


def _fully_qualified_ref(source_ref: str) -> str:
    ref = source_ref.strip()
    if not ref or len(ref) > 255 or any(ord(character) < 32 for character in ref):
        raise AzurePipelineError(
            "invalid_source_ref", "Azure pipeline refs must be valid branch or tag names."
        )
    if ref.startswith(("refs/heads/", "refs/tags/")):
        return ref
    if ref.startswith("refs/"):
        raise AzurePipelineError(
            "invalid_source_ref", "Azure pipeline refs must be branches or tags."
        )
    return f"refs/heads/{ref}"


def dispatch_pipeline(
    build_run: BuildRun,
    configuration: AzurePipelineConfiguration,
    *,
    transport: httpx.BaseTransport | None = None,
) -> AzurePipelineDispatchResult:
    project, _repository = parse_azure_repository(
        build_run.repository_url or "", configuration.organization_url
    )
    binding = pipeline_binding_for_repository(
        build_run.repository_url or "",
        configuration.organization_url,
        configuration.pipeline_bindings,
    )
    if build_run.workflow_path != azure_pipeline_reference(binding):
        raise AzurePipelineError(
            "azure_pipeline_mapping_changed",
            "The configured Azure pipeline no longer matches the pipeline selected for this build.",
        )

    organization_url = configuration.organization_url.rstrip("/")
    endpoint = (
        f"{organization_url}/{quote(project, safe='')}/_apis/pipelines/"
        f"{binding.pipeline_id}/runs?api-version=7.0"
    )
    callback_url = (
        f"{configuration.callback_url.rstrip('/')}/api/v1/build-runs/{build_run.id}/callback"
    )
    ref_name = _fully_qualified_ref(build_run.source_ref)
    body = {
        "resources": {"repositories": {"self": {"refName": ref_name}}},
        "variables": {
            "DEPLOYFORGE_BUILD_RUN_ID": {"value": str(build_run.id)},
            "DEPLOYFORGE_DEPLOYMENT_ID": {"value": str(build_run.deployment_id)},
            "DEPLOYFORGE_CALLBACK_URL": {"value": callback_url},
            "DEPLOYFORGE_SIGNER_IDENTITY": {"value": binding.signer_identity},
            "DEPLOYFORGE_OIDC_ISSUER": {"value": configuration.oidc_issuer},
        },
    }
    basic_auth = base64.b64encode(f"deployforge:{configuration.token}".encode()).decode("ascii")
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
                    "Authorization": f"Basic {basic_auth}",
                    "Content-Type": "application/json",
                    "User-Agent": "DeployForge-Control-Plane",
                },
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise AzurePipelineError(
            "azure_pipeline_timeout",
            "Azure DevOps did not respond before the pipeline dispatch timeout.",
            retryable=True,
        ) from exc
    except httpx.HTTPError as exc:
        raise AzurePipelineError(
            "azure_pipeline_unavailable",
            "Azure DevOps could not be reached for pipeline dispatch.",
            retryable=True,
        ) from exc

    if response.status_code == 429 or response.status_code >= 500:
        raise AzurePipelineError(
            "azure_pipeline_temporarily_unavailable",
            "Azure DevOps temporarily rejected the pipeline dispatch.",
            retryable=True,
        )
    if response.status_code in {401, 403}:
        raise AzurePipelineError(
            "azure_pipeline_permission_denied",
            "Azure DevOps rejected the credential or its pipeline permissions.",
        )
    if response.status_code in {400, 404, 409, 422}:
        raise AzurePipelineError(
            "azure_pipeline_unavailable",
            "Azure DevOps rejected the pipeline ID, ref, or pipeline configuration.",
        )
    if response.status_code != 200:
        raise AzurePipelineError(
            "azure_pipeline_rejected",
            f"Azure DevOps rejected pipeline dispatch with HTTP {response.status_code}.",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise AzurePipelineError(
            "azure_pipeline_invalid_response",
            "Azure DevOps returned invalid pipeline JSON.",
            retryable=True,
        ) from exc
    run_id = payload.get("id") if isinstance(payload, dict) else None
    links = payload.get("_links", {}) if isinstance(payload, dict) else {}
    web_link = links.get("web", {}) if isinstance(links, dict) else {}
    run_url = web_link.get("href") if isinstance(web_link, dict) else None
    if isinstance(run_id, bool) or not isinstance(run_id, (int, str)) or not str(run_id).isdigit():
        raise AzurePipelineError(
            "azure_pipeline_invalid_response",
            "Azure DevOps returned a pipeline run without a valid ID.",
            retryable=True,
        )
    if not isinstance(run_url, str):
        raise AzurePipelineError(
            "azure_pipeline_invalid_response",
            "Azure DevOps returned a pipeline run without a web URL.",
            retryable=True,
        )
    parsed_run_url = urlsplit(run_url)
    parsed_org_url = urlsplit(configuration.organization_url)
    expected_prefix = f"{parsed_org_url.path.rstrip('/')}/{quote(project, safe='')}/_build/"
    try:
        same_port = parsed_run_url.port == parsed_org_url.port
    except ValueError:
        same_port = False
    if (
        parsed_run_url.scheme != "https"
        or parsed_run_url.hostname != parsed_org_url.hostname
        or not same_port
        or parsed_run_url.username
        or parsed_run_url.password
        or not unquote(parsed_run_url.path)
        .casefold()
        .startswith(unquote(expected_prefix).casefold())
        or parsed_run_url.fragment
    ):
        raise AzurePipelineError(
            "azure_pipeline_invalid_response",
            "Azure DevOps returned a pipeline URL outside the configured organization and project.",
        )
    return AzurePipelineDispatchResult(str(run_id), run_url)
