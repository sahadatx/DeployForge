import json
import re
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

WorkspaceRole = Literal["workspace_admin", "platform_engineer", "service_owner", "auditor"]
ServiceLifecycle = Literal["experimental", "production", "deprecated", "retired"]
ServiceCriticality = Literal["tier_0", "tier_1", "tier_2", "tier_3"]
ArgoApplicationName = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$", min_length=1, max_length=253
    ),
]
WorkspaceSlug = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$", min_length=1, max_length=80
    ),
]
ServiceSlug = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$", min_length=1, max_length=100
    ),
]
TeamSlug = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$", min_length=1, max_length=80
    ),
]
CatalogTag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=48)]


def _safe_web_url(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must use http or https and include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("Credentials must not be embedded in catalog URLs")
    return cleaned


class WorkspaceContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    description: str | None = None
    created_at: datetime
    role: str | None = None


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    slug: WorkspaceSlug
    description: str | None = Field(default=None, max_length=2000)


class EnvironmentContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    slug: str
    name: str
    environment_type: str
    cluster_name: str | None = None
    namespace: str | None = None
    protected: bool
    allow_destroy: bool
    quota_cpu_millis: int | None = None
    quota_memory_mib: int | None = None
    quota_pods: int | None = None
    quota_persistent_volume_claims: int | None = None
    quota_services: int | None = None
    created_at: datetime


class EnvironmentCreate(BaseModel):
    slug: Annotated[
        str,
        StringConstraints(
            pattern=r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$", min_length=1, max_length=80
        ),
    ]
    name: str = Field(min_length=2, max_length=120)
    environment_type: Literal["development", "staging", "production", "preview"]
    cluster_name: str | None = Field(default=None, max_length=180)
    namespace: Annotated[
        str | None,
        StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", max_length=63),
    ] = None
    protected: bool | None = None
    allow_destroy: bool = False
    quota_cpu_millis: int | None = Field(default=None, gt=0, le=10_000_000)
    quota_memory_mib: int | None = Field(default=None, gt=0, le=10_000_000)
    quota_pods: int | None = Field(default=None, gt=0, le=100_000)
    quota_persistent_volume_claims: int | None = Field(default=None, gt=0, le=100_000)
    quota_services: int | None = Field(default=None, gt=0, le=100_000)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("name")
    @classmethod
    def require_nonblank_name(cls, value: str) -> str:
        if not value:
            raise ValueError("Environment name cannot be blank")
        return value


class EnvironmentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    cluster_name: str | None = Field(default=None, max_length=180)
    namespace: Annotated[
        str | None,
        StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", max_length=63),
    ] = None
    protected: bool | None = None
    allow_destroy: bool | None = None
    quota_cpu_millis: int | None = Field(default=None, gt=0, le=10_000_000)
    quota_memory_mib: int | None = Field(default=None, gt=0, le=10_000_000)
    quota_pods: int | None = Field(default=None, gt=0, le=100_000)
    quota_persistent_volume_claims: int | None = Field(default=None, gt=0, le=100_000)
    quota_services: int | None = Field(default=None, gt=0, le=100_000)

    @field_validator("name", "cluster_name", mode="before")
    @classmethod
    def strip_optional_names(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("name")
    @classmethod
    def reject_blank_name(cls, value: str | None) -> str | None:
        if value == "":
            raise ValueError("Environment name cannot be blank")
        return value


class InfrastructureTargetCreate(BaseModel):
    environment_id: UUID
    repository_url: str = Field(min_length=1, max_length=500)
    source_ref: str = Field(default="main", min_length=1, max_length=255)
    working_directory: str = Field(default=".", min_length=1, max_length=500)
    tool: Literal["terraform", "opentofu"] = "terraform"
    backend_workspace: str | None = Field(default=None, max_length=180)
    enabled: bool = True

    @field_validator("repository_url")
    @classmethod
    def validate_infrastructure_repository(cls, value: str) -> str:
        cleaned = value.strip().removesuffix(".git")
        parsed = urlsplit(cleaned)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or len(parsed.path.strip("/").split("/")) != 2
        ):
            raise ValueError("Infrastructure repository must be a canonical HTTPS GitHub URL")
        return cleaned

    @field_validator("source_ref")
    @classmethod
    def validate_source_ref(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(char.isspace() or ord(char) < 32 for char in cleaned):
            raise ValueError("Source ref cannot be blank or contain whitespace/control characters")
        return cleaned

    @field_validator("working_directory")
    @classmethod
    def validate_working_directory(cls, value: str) -> str:
        raw = value.strip()
        if raw.startswith("/") or "\\" in raw:
            raise ValueError("Working directory must remain inside the checked out repository")
        cleaned = raw.strip("/") or "."
        if any(part in {"..", ".git"} for part in cleaned.split("/")):
            raise ValueError("Working directory must remain inside the checked out repository")
        return cleaned


class InfrastructureTargetPatch(BaseModel):
    repository_url: str | None = Field(default=None, min_length=1, max_length=500)
    source_ref: str | None = Field(default=None, min_length=1, max_length=255)
    working_directory: str | None = Field(default=None, min_length=1, max_length=500)
    tool: Literal["terraform", "opentofu"] | None = None
    backend_workspace: str | None = Field(default=None, max_length=180)
    enabled: bool | None = None


class InfrastructureTargetContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    environment_id: UUID
    repository_url: str
    source_ref: str
    working_directory: str
    tool: str
    backend_workspace: str | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class InfrastructureRunCreate(BaseModel):
    run_kind: Literal["plan", "drift"] = "plan"


class InfrastructurePlanCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_id: str = Field(min_length=1, max_length=180)
    operation: Literal["plan", "apply"]
    status: Literal["running", "planned", "succeeded", "failed"]
    external_run_id: str | None = Field(default=None, max_length=180)
    run_url: str | None = Field(default=None, max_length=1000)
    plan_digest: str | None = Field(default=None, max_length=80)
    plan_artifact_uri: str | None = Field(default=None, max_length=1000)
    plan_has_changes: bool | None = None
    resource_additions: int | None = Field(default=None, ge=0)
    resource_changes: int | None = Field(default=None, ge=0)
    resource_destroys: int | None = Field(default=None, ge=0)
    quota_usage: dict[str, int] = Field(default_factory=dict)
    failure_code: str | None = Field(default=None, max_length=80)

    @field_validator("plan_digest")
    @classmethod
    def validate_plan_digest(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("Plan digest must be a lowercase SHA-256 digest")
        return value

    @field_validator("run_url", "plan_artifact_uri")
    @classmethod
    def validate_infrastructure_evidence_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Infrastructure run and artifact URLs must be stable, "
                "credential-free GitHub HTTPS links"
            )
        return value

    @field_validator("failure_code")
    @classmethod
    def validate_failure_code(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,79}", value):
            raise ValueError("Failure code must be a lowercase identifier")
        return value

    @field_validator("quota_usage")
    @classmethod
    def validate_quota_usage(cls, value: dict[str, int]) -> dict[str, int]:
        allowed = {
            "cpu_millis",
            "memory_mib",
            "pods",
            "persistent_volume_claims",
            "services",
        }
        if set(value) - allowed or any(
            isinstance(item, bool) or item < 0 for item in value.values()
        ):
            raise ValueError("Quota usage must contain nonnegative supported resource totals")
        return value


class InfrastructureDecision(BaseModel):
    comment: str | None = Field(default=None, max_length=500)

    @field_validator("comment", mode="before")
    @classmethod
    def strip_comment(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class InfrastructureRunContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    environment_id: UUID
    target_id: UUID | None = None
    requested_by: UUID | None = None
    approved_by: UUID | None = None
    attempt_number: int
    attempt_count: int
    max_attempts: int
    run_kind: str
    active_operation: str | None = None
    status: str
    source_ref: str
    working_directory: str
    external_run_id: str | None = None
    run_url: str | None = None
    plan_digest: str | None = None
    plan_artifact_uri: str | None = None
    plan_has_changes: bool | None = None
    resource_additions: int | None = None
    resource_changes: int | None = None
    resource_destroys: int | None = None
    quota_usage: dict[str, int] = Field(default_factory=dict)
    callback_deadline_at: datetime | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class InfrastructureRunEventContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    infrastructure_run_id: UUID
    workspace_id: UUID
    sequence: int
    delivery_id: str | None = None
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    actor_type: str
    actor_id: str | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class InfrastructureRunDetail(InfrastructureRunContract):
    events: list[InfrastructureRunEventContract] = Field(default_factory=list)


PolicyAction = Literal["deployment", "infrastructure"]
PolicyEffect = Literal["deny", "require_approval", "warn"]
PolicyOperator = Literal[
    "equals",
    "not_equals",
    "in",
    "not_in",
    "greater_than",
    "greater_than_or_equal",
    "less_than",
    "less_than_or_equal",
    "contains",
    "not_contains",
    "exists",
    "not_exists",
]
PolicyField = Literal[
    "service.slug",
    "service.lifecycle",
    "service.criticality",
    "service.tags",
    "service.repository_url",
    "environment.slug",
    "environment.type",
    "environment.protected",
    "deployment.version",
    "deployment.source_ref",
    "artifact.all_signed",
    "artifact.all_have_sbom",
    "artifact.all_have_provenance",
    "artifact.critical_vulnerabilities",
    "artifact.high_vulnerabilities",
    "infrastructure.tool",
    "infrastructure.source_ref",
    "infrastructure.plan_has_changes",
    "infrastructure.resource_additions",
    "infrastructure.resource_changes",
    "infrastructure.resource_destroys",
    "infrastructure.quota_usage.cpu_millis",
    "infrastructure.quota_usage.memory_mib",
    "infrastructure.quota_usage.pods",
    "infrastructure.quota_usage.persistent_volume_claims",
    "infrastructure.quota_usage.services",
]
PolicySlug = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$", min_length=1, max_length=100
    ),
]


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: PolicySlug
    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    field: PolicyField
    operator: PolicyOperator
    expected: Any = None
    effect: PolicyEffect

    @model_validator(mode="after")
    def validate_condition(self):
        existence_operator = self.operator in {"exists", "not_exists"}
        if existence_operator and "expected" in self.model_fields_set:
            raise ValueError("exists operators do not accept an expected value")
        if not existence_operator and "expected" not in self.model_fields_set:
            raise ValueError("This operator requires an expected value")
        if self.operator in {"in", "not_in"} and (
            not isinstance(self.expected, list) or not self.expected or len(self.expected) > 100
        ):
            raise ValueError("in operators require a nonempty list of at most 100 values")
        if self.operator in {
            "greater_than",
            "greater_than_or_equal",
            "less_than",
            "less_than_or_equal",
        } and (isinstance(self.expected, bool) or not isinstance(self.expected, (int, float))):
            raise ValueError("Numeric comparison operators require a numeric expected value")
        try:
            encoded = json.dumps(self.expected, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Expected value must be valid JSON") from exc
        if len(encoded.encode("utf-8")) > 8192:
            raise ValueError("Expected value cannot exceed 8 KiB")
        return self


class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: PolicySlug
    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool = True
    enforcement_mode: Literal["enforce", "monitor"] = "enforce"
    actions: list[PolicyAction] = Field(min_length=1, max_length=2)
    rules: list[PolicyRule] = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def strip_policy_name(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_policy_document(self):
        if not self.name:
            raise ValueError("Policy name cannot be blank")
        if len(set(self.actions)) != len(self.actions):
            raise ValueError("Policy actions must be unique")
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("Policy rule IDs must be unique within a policy")
        for rule in self.rules:
            field_action = rule.field.split(".", 1)[0]
            required_action = {
                "service": "deployment",
                "deployment": "deployment",
                "artifact": "deployment",
                "infrastructure": "infrastructure",
            }.get(field_action)
            if required_action and required_action not in self.actions:
                raise ValueError(f"Rule {rule.id} uses a field outside the policy actions")
        return self


class PolicyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    enforcement_mode: Literal["enforce", "monitor"] | None = None
    actions: list[PolicyAction] | None = Field(default=None, min_length=1, max_length=2)
    rules: list[PolicyRule] | None = Field(default=None, min_length=1, max_length=100)


class PolicyContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    slug: str
    name: str
    description: str | None = None
    enabled: bool
    enforcement_mode: str
    actions: list[str]
    rules: list[PolicyRule]
    version: int
    created_by: UUID | None = None
    updated_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class PolicyServiceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str | None = None
    lifecycle: str | None = None
    criticality: str | None = None
    tags: list[str] | None = None
    repository_url: str | None = None


class PolicyEnvironmentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str | None = None
    type: str | None = None
    protected: bool | None = None


class PolicyDeploymentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str | None = None
    source_ref: str | None = None


class PolicyArtifactContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    all_signed: bool | None = None
    all_have_sbom: bool | None = None
    all_have_provenance: bool | None = None
    critical_vulnerabilities: int | None = Field(default=None, ge=0)
    high_vulnerabilities: int | None = Field(default=None, ge=0)


class PolicyInfrastructureContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Literal["terraform", "opentofu"] | None = None
    source_ref: str | None = None
    plan_has_changes: bool | None = None
    resource_additions: int | None = Field(default=None, ge=0)
    resource_changes: int | None = Field(default=None, ge=0)
    resource_destroys: int | None = Field(default=None, ge=0)
    quota_usage: dict[str, int] = Field(default_factory=dict)


class PolicyEvaluationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: PolicyServiceContext | None = None
    environment: PolicyEnvironmentContext | None = None
    deployment: PolicyDeploymentContext | None = None
    artifact: PolicyArtifactContext | None = None
    infrastructure: PolicyInfrastructureContext | None = None


class PolicyEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: PolicyAction
    service_id: UUID | None = None
    environment_id: UUID | None = None
    context: PolicyEvaluationContext = Field(default_factory=PolicyEvaluationContext)

    @model_validator(mode="after")
    def bound_context_size(self):
        encoded = json.dumps(
            self.context.model_dump(mode="json", exclude_none=True),
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > 32_768:
            raise ValueError("Policy evaluation context cannot exceed 32 KiB")
        return self


class PolicyEvaluationContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    action: str
    subject_type: str
    subject_id: str
    decision: str
    requires_approval: bool
    context: dict[str, Any]
    policy_snapshot: list[dict[str, Any]]
    policy_snapshot_sha256: str
    results: list[dict[str, Any]]
    created_at: datetime


class PolicyExceptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: PolicySlug
    service_id: UUID | None = None
    environment_id: UUID | None = None
    reason: str = Field(min_length=10, max_length=2000)
    expires_at: datetime

    @model_validator(mode="after")
    def require_narrow_scope(self):
        if self.service_id is None and self.environment_id is None:
            raise ValueError("Policy exceptions must be scoped to a service or environment")
        if self.expires_at.tzinfo is None:
            raise ValueError("Exception expiry must include a timezone")
        return self

    @field_validator("reason", mode="before")
    @classmethod
    def strip_exception_reason(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("reason")
    @classmethod
    def reject_blank_exception_reason(cls, value: str) -> str:
        if len(value) < 10:
            raise ValueError("Exception reason must contain at least 10 nonblank characters")
        return value


class PolicyExceptionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=1000)

    @field_validator("comment", mode="before")
    @classmethod
    def strip_exception_comment(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class PolicyExceptionContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    policy_id: UUID
    policy_version: int
    rule_id: str
    service_id: UUID | None = None
    environment_id: UUID | None = None
    reason: str
    status: str
    requested_by: UUID | None = None
    reviewed_by: UUID | None = None
    review_comment: str | None = None
    expires_at: datetime
    reviewed_at: datetime | None = None
    created_at: datetime


class ServiceContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    slug: str
    name: str
    description: str | None = None
    system: str | None = None
    owner_team_id: UUID | None = None
    owner_team_name: str | None = None
    repository_url: str | None = None
    documentation_url: str | None = None
    lifecycle: ServiceLifecycle
    criticality: ServiceCriticality
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    golden_path_id: UUID | None = None
    golden_path_slug: str | None = None
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class ServiceCatalogResponse(BaseModel):
    items: list[ServiceContract]
    total: int
    limit: int
    offset: int


class ServiceCatalogSummary(BaseModel):
    total: int
    production: int
    experimental: int
    ownership_teams: int


class ServiceTeamContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    slug: str
    name: str
    description: str | None = None
    service_count: int = 0
    created_at: datetime
    updated_at: datetime


class ServiceTeamCreate(BaseModel):
    slug: TeamSlug
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class GoldenPathContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    description: str
    category: str
    icon: str
    version: str
    status: str
    capabilities: list[str] = Field(default_factory=list)
    config_schema: list[dict[str, Any]] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)


class ServiceCreate(BaseModel):
    slug: ServiceSlug
    name: str = Field(min_length=2, max_length=180)
    description: str | None = Field(default=None, max_length=5000)
    system: str | None = Field(default=None, max_length=120)
    owner_team_id: UUID
    repository_url: str | None = Field(default=None, max_length=500)
    documentation_url: str | None = Field(default=None, max_length=500)
    lifecycle: ServiceLifecycle = "experimental"
    criticality: ServiceCriticality = "tier_2"
    tags: list[CatalogTag] = Field(default_factory=list, max_length=20)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=50)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("repository_url", "documentation_url")
    @classmethod
    def validate_catalog_urls(cls, value: str | None) -> str | None:
        return _safe_web_url(value)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for value in values:
            key = value.casefold()
            if key not in seen:
                normalized.append(value)
                seen.add(key)
        return normalized


class ServiceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=180)
    description: str | None = Field(default=None, max_length=5000)
    system: str | None = Field(default=None, max_length=120)
    owner_team_id: UUID | None = None
    repository_url: str | None = Field(default=None, max_length=500)
    documentation_url: str | None = Field(default=None, max_length=500)
    lifecycle: ServiceLifecycle | None = None
    criticality: ServiceCriticality | None = None
    tags: list[CatalogTag] | None = Field(default=None, max_length=20)
    metadata: dict[str, Any] | None = Field(default=None, max_length=50)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("repository_url", "documentation_url")
    @classmethod
    def validate_catalog_urls(cls, value: str | None) -> str | None:
        return _safe_web_url(value)

    @field_validator("lifecycle", "criticality")
    @classmethod
    def reject_null_status(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("This field cannot be null")
        return value

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return []
        seen: set[str] = set()
        normalized: list[str] = []
        for value in values:
            key = value.casefold()
            if key not in seen:
                normalized.append(value)
                seen.add(key)
        return normalized


class GoldenPathInstantiate(ServiceCreate):
    pass


class DeploymentContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    service_id: UUID
    environment_id: UUID
    version: str
    source_ref: str | None = None
    status: str
    requested_by: str | None = None
    idempotency_key: str | None = None
    attempt_count: int = 0
    max_attempts: int = 3
    started_at: datetime | None = None
    finished_at: datetime | None = None
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    created_at: datetime
    updated_at: datetime


class DeploymentRequest(BaseModel):
    service_id: UUID
    environment_id: UUID
    version: str = Field(min_length=1, max_length=160)
    source_ref: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("version", mode="before")
    @classmethod
    def strip_version(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("source_ref", mode="before")
    @classmethod
    def strip_source_ref(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class DeploymentEventContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    deployment_id: UUID
    workspace_id: UUID
    sequence: int
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    actor_type: str
    actor_id: str | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class DeploymentApprovalRequest(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=1000)


class DeploymentRetryResponse(BaseModel):
    deployment: DeploymentContract
    message: str


class GitOpsTargetCreate(BaseModel):
    service_id: UUID
    environment_id: UUID
    application_name: ArgoApplicationName
    expected_project: str = Field(default="default", min_length=1, max_length=80)
    expected_cluster_name: str | None = Field(default=None, max_length=180)
    artifact_name: str | None = Field(default=None, min_length=1, max_length=180)
    enabled: bool = True


class GitOpsTargetPatch(BaseModel):
    application_name: ArgoApplicationName | None = None
    expected_project: str | None = Field(default=None, min_length=1, max_length=80)
    expected_cluster_name: str | None = Field(default=None, max_length=180)
    artifact_name: str | None = Field(default=None, min_length=1, max_length=180)
    enabled: bool | None = None


class GitOpsTargetContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    service_id: UUID
    environment_id: UUID
    application_name: str
    expected_project: str
    expected_cluster_name: str | None = None
    artifact_name: str | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ArgoApplicationStatus(BaseModel):
    application_name: str
    provider_status: str
    failure_code: str | None = None
    project: str | None = None
    sync_status: str | None = None
    health_status: str | None = None
    operation_phase: str | None = None
    revision: str | None = None
    destination_name: str | None = None
    destination_server: str | None = None


class GitOpsRunContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    deployment_id: UUID
    build_run_id: UUID | None = None
    target_id: UUID | None = None
    rollback_from_run_id: UUID | None = None
    attempt_number: int
    action_kind: str
    application_name: str
    desired_revision: str | None = None
    observed_revision: str | None = None
    artifact_uri: str | None = None
    artifact_digest: str | None = None
    status: str
    sync_status: str | None = None
    health_status: str | None = None
    operation_phase: str | None = None
    destination_name: str | None = None
    destination_server: str | None = None
    attempt_count: int
    max_attempts: int
    callback_deadline_at: datetime | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class GitOpsRunEventContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    gitops_run_id: UUID
    workspace_id: UUID
    sequence: int
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    actor_type: str
    actor_id: str | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class GitOpsRunDetail(GitOpsRunContract):
    events: list[GitOpsRunEventContract] = Field(default_factory=list)


class BuildRunContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    deployment_id: UUID
    service_id: UUID
    attempt_number: int
    provider: str
    repository_url: str | None = None
    workflow_path: str
    source_ref: str
    commit_sha: str | None = None
    gitops_revision: str | None = None
    status: str
    attempt_count: int
    max_attempts: int
    external_run_id: str | None = None
    run_url: str | None = None
    callback_deadline_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    created_at: datetime
    updated_at: datetime


class BuildRunEventContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    build_run_id: UUID
    workspace_id: UUID
    sequence: int
    delivery_id: str | None = None
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    actor_type: str
    actor_id: str | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class VulnerabilitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    critical: int = Field(default=0, ge=0, le=1_000_000)
    high: int = Field(default=0, ge=0, le=1_000_000)
    medium: int = Field(default=0, ge=0, le=1_000_000)
    low: int = Field(default=0, ge=0, le=1_000_000)
    unknown: int = Field(default=0, ge=0, le=1_000_000)


Digest = Annotated[
    str,
    StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$", min_length=71, max_length=71),
]


def _safe_evidence_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"https", "oci"} or not parsed.netloc:
        raise ValueError("Evidence locations must use https or oci and include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("Credentials must not be embedded in evidence URLs")
    return value.strip()


class BuildArtifactEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=180)
    artifact_type: Literal["container", "package", "binary"]
    uri: str = Field(max_length=1000)
    digest: Digest
    sbom_format: Literal["cyclonedx-json", "spdx-json"]
    sbom_uri: str = Field(max_length=1000)
    sbom_digest: Digest
    vulnerability_summary: VulnerabilitySummary
    signature_uri: str = Field(max_length=1000)
    signature_bundle: dict[str, Any] | None = None
    signer_identity: str = Field(min_length=1, max_length=500)
    oidc_issuer: str = Field(min_length=1, max_length=500)
    provenance_uri: str = Field(max_length=1000)
    provenance_digest: Digest
    provenance_bundle: dict[str, Any] | None = None

    @field_validator("uri", "sbom_uri", "signature_uri", "provenance_uri")
    @classmethod
    def validate_evidence_locations(cls, value: str) -> str:
        return _safe_evidence_url(value)

    @field_validator("name", "signer_identity", "oidc_issuer", mode="before")
    @classmethod
    def strip_evidence_labels(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("signature_bundle", "provenance_bundle")
    @classmethod
    def limit_inline_bundle_size(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        bundle_size = (
            len(json.dumps(value, separators=(",", ":")).encode("utf-8"))
            if value is not None
            else 0
        )
        if bundle_size > 100 * 1024:
            raise ValueError("Each inline Sigstore bundle must be at most 100 KiB")
        return value


class BuildCallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    repository_url: str = Field(min_length=1, max_length=500)
    external_run_id: str = Field(min_length=1, max_length=120)
    run_url: str = Field(max_length=1000)
    commit_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40,64}$")]
    gitops_revision: Annotated[
        str | None, StringConstraints(pattern=r"^[0-9a-f]{40,64}$")
    ] = None
    status: Literal["running", "succeeded", "failed"]
    failure_message: str | None = Field(default=None, max_length=1000)
    artifacts: list[BuildArtifactEvidence] = Field(default_factory=list, max_length=20)

    @field_validator("repository_url", "run_url")
    @classmethod
    def validate_callback_urls(cls, value: str) -> str:
        return _safe_web_url(value) or ""

    @field_validator("artifacts")
    @classmethod
    def reject_duplicate_artifact_digests(
        cls, values: list[BuildArtifactEvidence]
    ) -> list[BuildArtifactEvidence]:
        digests = [artifact.digest for artifact in values]
        if len(digests) != len(set(digests)):
            raise ValueError("An artifact digest may only be reported once per callback")
        return values


class BuildArtifactContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    build_run_id: UUID
    name: str
    artifact_type: str
    uri: str
    digest: str
    sbom_format: str
    sbom_uri: str
    sbom_digest: str
    vulnerability_summary: dict[str, int]
    signature_uri: str
    signer_identity: str
    oidc_issuer: str
    provenance_uri: str
    provenance_digest: str
    evidence_status: str
    created_at: datetime


class BuildRunDetail(BuildRunContract):
    events: list[BuildRunEventContract] = Field(default_factory=list)
    artifacts: list[BuildArtifactContract] = Field(default_factory=list)


class AuditEventContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID | None = None
    actor_type: str
    actor_id: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    outcome: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class UserContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    display_name: str
    email_verified: bool
    is_platform_admin: bool
    active: bool
    created_at: datetime


class AuthSessionContract(BaseModel):
    user: UserContract
    memberships: list[dict[str, Any]]
    csrf_token: str


class WorkspaceMemberContract(BaseModel):
    membership_id: UUID
    user: UserContract
    role: WorkspaceRole
    created_at: datetime


class MembershipCreate(BaseModel):
    email: EmailStr
    role: WorkspaceRole


class MembershipUpdate(BaseModel):
    role: WorkspaceRole


class TelemetrySourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=120)
    service_id: UUID
    environment_id: UUID | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_source_name(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("name")
    @classmethod
    def require_nonblank_source_name(cls, value: str) -> str:
        if not value:
            raise ValueError("Telemetry source name cannot be blank")
        return value


class TelemetrySourceContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    service_id: UUID
    environment_id: UUID | None = None
    name: str
    token_prefix: str
    enabled: bool
    created_by: UUID | None = None
    created_at: datetime
    last_seen_at: datetime | None = None


class TelemetrySourceCreated(TelemetrySourceContract):
    token: str


class TelemetrySourcePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class TelemetrySampleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=8, max_length=64),
    ]
    observed_at: AwareDatetime
    interval_seconds: int = Field(ge=30, le=3600)
    request_count: int = Field(ge=0, le=10_000_000)
    error_count: int = Field(ge=0, le=10_000_000)
    latency_bucket_counts: dict[str, int] = Field(default_factory=dict, max_length=6)

    @model_validator(mode="after")
    def validate_aggregate(self) -> "TelemetrySampleIn":
        if self.error_count > self.request_count:
            raise ValueError("error_count cannot exceed request_count")
        valid_buckets = {"100", "300", "500", "1000", "2500", "5000"}
        if not set(self.latency_bucket_counts).issubset(valid_buckets):
            raise ValueError("Latency bucket keys must use supported millisecond bounds")
        counts = [
            self.latency_bucket_counts[key]
            for key in ("100", "300", "500", "1000", "2500", "5000")
            if key in self.latency_bucket_counts
        ]
        if any(count < 0 or count > self.request_count for count in counts):
            raise ValueError("Latency bucket counts must be within request_count")
        if counts != sorted(counts):
            raise ValueError("Latency bucket counts must be cumulative and non-decreasing")
        return self


class TelemetryBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    samples: list[TelemetrySampleIn] = Field(min_length=1, max_length=250)

    @field_validator("samples")
    @classmethod
    def reject_duplicate_delivery_keys(cls, values: list[TelemetrySampleIn]):
        keys = [sample.idempotency_key for sample in values]
        if len(keys) != len(set(keys)):
            raise ValueError("A telemetry batch cannot repeat an idempotency_key")
        return values


class TelemetryBatchResult(BaseModel):
    accepted: int
    duplicates: int


class ServiceLevelObjectiveCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: Annotated[
        str,
        StringConstraints(
            pattern=r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$", min_length=1, max_length=100
        ),
    ]
    name: str = Field(min_length=2, max_length=160)
    service_id: UUID
    environment_id: UUID | None = None
    indicator: Literal["availability", "latency"]
    objective: float = Field(gt=0, lt=1)
    window_hours: int = Field(ge=1, le=720)
    latency_threshold_ms: Literal[100, 300, 500, 1000, 2500, 5000] | None = None

    @model_validator(mode="after")
    def validate_indicator(self) -> "ServiceLevelObjectiveCreate":
        if self.indicator == "latency" and self.latency_threshold_ms is None:
            raise ValueError("Latency objectives require latency_threshold_ms")
        if self.indicator == "availability" and self.latency_threshold_ms is not None:
            raise ValueError("Availability objectives cannot set latency_threshold_ms")
        return self

    @field_validator("name", mode="before")
    @classmethod
    def strip_slo_name(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("name")
    @classmethod
    def require_nonblank_slo_name(cls, value: str) -> str:
        if not value:
            raise ValueError("SLO name cannot be blank")
        return value


class ServiceLevelObjectiveContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    service_id: UUID
    environment_id: UUID | None = None
    slug: str
    name: str
    indicator: str
    objective: float
    window_hours: int
    latency_threshold_ms: int | None = None
    enabled: bool
    created_by: UUID | None = None
    created_at: datetime
    last_evaluated_at: datetime | None = None


class SloEvaluationContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    slo_id: UUID
    status: str
    window_start: datetime
    window_end: datetime
    total_events: int
    good_events: int
    attainment: float | None = None
    error_budget_remaining: float | None = None
    burn_rate: float | None = None
    created_at: datetime


class SloAlertContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    slo_id: UUID
    evaluation_id: UUID
    status: str
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    acknowledged_by: UUID | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    created_at: datetime
