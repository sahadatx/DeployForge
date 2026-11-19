import json
import os
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _port_is_valid(parsed) -> bool:
    try:
        port = parsed.port
    except ValueError:
        return False
    return port is None or 0 < port <= 65535


def _validate_port(parsed, setting_name: str) -> None:
    if not _port_is_valid(parsed):
        raise ValueError(f"{setting_name} contains an invalid port")


class Settings(BaseSettings):
    app_name: str = "DeployForge Enterprise"
    app_env: str = "development"
    runtime_role: str = "api"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./deployforge.db"
    database_pool_size: int = Field(default=3, ge=1, le=100)
    database_max_overflow: int = Field(default=2, ge=0, le=100)
    database_pool_timeout_seconds: int = Field(default=30, ge=1, le=300)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60, le=86400)
    database_require_ssl: bool = False
    database_sslmode: str = ""
    database_sslrootcert: str = ""
    worker_heartbeat_interval_seconds: int = 10
    worker_heartbeat_stale_after_seconds: int = 30
    telemetry_retention_days: int = 31
    github_actions_token: SecretStr | None = None
    github_repository_token: SecretStr | None = None
    github_server_url: str = "https://github.com"
    github_api_url: str = "https://api.github.com"
    build_provider: str = "github_actions"
    gitlab_server_url: str = ""
    gitlab_api_url: str = ""
    gitlab_ci_config_path: str = ".gitlab-ci.yml"
    gitlab_build_token: SecretStr | None = None
    azure_devops_organization_url: str = ""
    azure_devops_oidc_issuer: str = ""
    azure_devops_pipeline_ids: str = "{}"
    azure_devops_build_token: SecretStr | None = None
    github_repository_owner: str = ""
    github_repository_templates: str = "{}"
    github_repository_private: bool = True
    github_actions_workflow_path: str = ".github/workflows/deployforge-build.yml"
    infrastructure_workflow_path: str = ".github/workflows/deployforge-infrastructure.yml"
    build_callback_secret: SecretStr | None = None
    build_callback_public_url: str = ""
    build_callback_timeout_seconds: int = 14_400
    build_max_critical_vulnerabilities: int = 0
    build_max_high_vulnerabilities: int = 0
    require_verified_supply_chain: bool = False
    sigstore_oidc_issuer: str = "https://token.actions.githubusercontent.com"
    sigstore_trusted_root_file: str = ""
    sigstore_trusted_root_sha256: str = ""
    apk_parser_url: str = ""
    apk_parser_timeout_seconds: int = Field(default=120, ge=5, le=600)
    argocd_api_url: str = ""
    argocd_auth_token: SecretStr | None = None
    argocd_api_timeout_seconds: int = 15
    flux_api_url: str = ""
    flux_auth_token: SecretStr | None = None
    flux_cluster_name: str = ""
    flux_api_timeout_seconds: int = Field(default=15, ge=1, le=60)
    argo_rollouts_api_url: str = ""
    argo_rollouts_auth_token: SecretStr | None = None
    argo_rollouts_api_timeout_seconds: int = Field(default=15, ge=1, le=60)
    gitops_sync_timeout_seconds: int = 1_800
    gitops_reconcile_interval_seconds: int = 10
    session_secret: SecretStr | None = None
    session_cookie_name: str = "deployforge_session"
    session_cookie_secure: bool = False
    session_ttl_seconds: int = 43_200
    oidc_discovery_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr | None = None
    oidc_redirect_uri: str = ""
    oidc_admin_emails: str = ""
    oidc_group_claim: str = "groups"
    oidc_scim_external_id_claim: str = "external_id"
    oidc_group_role_mappings: str = "{}"
    scim_bearer_token: SecretStr | None = None
    pagerduty_routing_key: SecretStr | None = None
    frontend_base_url: str = "http://localhost:5173"
    dev_auth_enabled: bool = False
    dev_auth_auto_admin_first_user: bool = False
    require_verified_approval_identity: bool = False

    @model_validator(mode="before")
    @classmethod
    def load_sensitive_values_from_files(cls, values):
        """Support the common *_FILE convention for container-mounted secrets."""
        if not isinstance(values, dict):
            return values
        resolved = dict(values)
        for field_name in (
            "database_url",
            "github_actions_token",
            "github_repository_token",
            "gitlab_build_token",
            "azure_devops_build_token",
            "build_callback_secret",
            "argocd_auth_token",
            "flux_auth_token",
            "argo_rollouts_auth_token",
            "session_secret",
            "oidc_client_secret",
            "scim_bearer_token",
            "pagerduty_routing_key",
        ):
            file_environment_name = f"DEPLOYFORGE_{field_name.upper()}_FILE"
            secret_file = os.environ.get(file_environment_name)
            if not secret_file:
                continue
            current_value = resolved.get(field_name)
            if current_value not in (None, ""):
                raise ValueError(
                    f"Configure either DEPLOYFORGE_{field_name.upper()} or its _FILE "
                    "setting, not both"
                )
            try:
                resolved[field_name] = Path(secret_file).read_text(encoding="utf-8").rstrip("\r\n")
            except OSError as exc:
                raise ValueError(f"Could not read {file_environment_name}") from exc
        return resolved

    @field_validator(
        "github_api_url",
        "github_server_url",
        "gitlab_server_url",
        "gitlab_api_url",
        "azure_devops_organization_url",
        "azure_devops_oidc_issuer",
        "argocd_api_url",
        "flux_api_url",
        "argo_rollouts_api_url",
        "oidc_discovery_url",
        "oidc_redirect_uri",
        "frontend_base_url",
        "apk_parser_url",
        "sigstore_oidc_issuer",
        mode="before",
    )
    @classmethod
    def trim_configured_urls(cls, value: str) -> str:
        return str(value).strip()

    @field_validator("sigstore_trusted_root_file", "sigstore_trusted_root_sha256", mode="before")
    @classmethod
    def trim_sigstore_trust_root_settings(cls, value: str) -> str:
        return str(value).strip()

    @field_validator("github_repository_owner", mode="before")
    @classmethod
    def trim_repository_owner(cls, value: str) -> str:
        return str(value).strip()

    @field_validator("flux_cluster_name", mode="before")
    @classmethod
    def trim_flux_cluster_name(cls, value: str) -> str:
        normalized = str(value).strip()
        if len(normalized) > 180 or any(ord(character) < 32 for character in normalized):
            raise ValueError("DEPLOYFORGE_FLUX_CLUSTER_NAME must be a valid cluster name")
        return normalized

    @field_validator("build_provider", mode="before")
    @classmethod
    def validate_build_provider(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in {"github_actions", "gitlab_ci", "azure_devops"}:
            raise ValueError(
                "DEPLOYFORGE_BUILD_PROVIDER must be github_actions, gitlab_ci, or azure_devops"
            )
        return normalized

    @field_validator("azure_devops_pipeline_ids")
    @classmethod
    def validate_azure_devops_pipeline_ids(cls, value: str) -> str:
        try:
            bindings = json.loads(value or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Azure DevOps pipeline bindings must be valid JSON") from exc
        if not isinstance(bindings, dict):
            raise ValueError("Azure DevOps pipeline bindings must be a JSON object")
        normalized: dict[str, dict[str, object]] = {}
        seen: set[str] = set()
        for repository, binding in bindings.items():
            if (
                not isinstance(repository, str)
                or repository.count("/") != 1
                or any(not part.strip() or part in {".", ".."} for part in repository.split("/"))
                or any(ord(character) < 32 for character in repository)
            ):
                raise ValueError(
                    "Azure DevOps pipeline binding keys must be Project/Repository names"
                )
            if repository.casefold() in seen:
                raise ValueError("Azure DevOps pipeline binding keys must be unique")
            seen.add(repository.casefold())
            if not isinstance(binding, dict):
                raise ValueError("Azure DevOps pipeline bindings must contain objects")
            pipeline_id = binding.get("pipeline_id")
            signer_identity = binding.get("signer_identity")
            config_path = binding.get("config_path", "azure-pipelines.yml")
            if (
                isinstance(pipeline_id, bool)
                or not isinstance(pipeline_id, int)
                or pipeline_id < 1
                or pipeline_id > 2_147_483_647
            ):
                raise ValueError("Azure DevOps pipeline_id values must be positive integers")
            if (
                not isinstance(signer_identity, str)
                or not signer_identity.strip()
                or len(signer_identity.strip()) > 229
                or any(ord(character) < 32 for character in signer_identity)
            ):
                raise ValueError(
                    "Azure DevOps signer_identity values must be HTTPS identities up to "
                    "229 characters"
                )
            parsed_signer = urlsplit(signer_identity.strip())
            if (
                parsed_signer.scheme != "https"
                or not parsed_signer.hostname
                or parsed_signer.username
                or parsed_signer.password
                or parsed_signer.query
                or parsed_signer.fragment
            ):
                raise ValueError("Azure DevOps signer_identity values must be HTTPS identities")
            if not isinstance(config_path, str):
                raise ValueError("Azure DevOps config_path values must be strings")
            path = config_path.strip()
            parts = path.split("/")
            if (
                not path
                or len(path) > 255
                or any(part in {"", ".", ".."} for part in parts)
                or not path.endswith((".yml", ".yaml"))
                or any(not re.fullmatch(r"[A-Za-z0-9_. -]+", part) for part in parts)
            ):
                raise ValueError("Azure DevOps config_path must be a safe YAML path")
            normalized[repository.strip()] = {
                "pipeline_id": pipeline_id,
                "signer_identity": signer_identity.strip(),
                "config_path": path,
            }
        return json.dumps(normalized, sort_keys=True, separators=(",", ":"))

    @field_validator("gitlab_ci_config_path")
    @classmethod
    def validate_gitlab_ci_config_path(cls, value: str) -> str:
        path = value.strip()
        parts = path.split("/")
        if (
            not path
            or len(path) > 500
            or any(part in {"", ".", ".."} for part in parts)
            or not path.endswith((".yml", ".yaml"))
            or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)
        ):
            raise ValueError("DEPLOYFORGE_GITLAB_CI_CONFIG_PATH must be a safe YAML path")
        return path

    @field_validator("github_repository_templates")
    @classmethod
    def validate_repository_templates(cls, value: str) -> str:
        try:
            templates = json.loads(value or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("GitHub repository templates must be valid JSON") from exc
        if not isinstance(templates, dict):
            raise ValueError("GitHub repository templates must be a JSON object")
        slug_pattern = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?\Z")
        repo_pattern = re.compile(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}\Z"
        )
        normalized = {}
        for slug, repository in templates.items():
            if not isinstance(slug, str) or not slug_pattern.fullmatch(slug):
                raise ValueError("GitHub repository template keys must be golden path slugs")
            if not isinstance(repository, str) or not repo_pattern.fullmatch(repository):
                raise ValueError("GitHub repository templates must use owner/repository names")
            owner, name = repository.split("/", 1)
            if owner in {".", ".."} or name in {".", ".."}:
                raise ValueError("GitHub repository template names are invalid")
            normalized[slug] = f"{owner}/{name}"
        return json.dumps(normalized, sort_keys=True, separators=(",", ":"))

    @field_validator("apk_parser_url")
    @classmethod
    def validate_apk_parser_url(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("DEPLOYFORGE_APK_PARSER_URL must be an internal HTTP(S) service URL")
        return value.rstrip("/")

    @field_validator("app_env", mode="before")
    @classmethod
    def normalize_app_environment(cls, value: str) -> str:
        return str(value).strip().lower()

    @field_validator("runtime_role", mode="before")
    @classmethod
    def normalize_runtime_role(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in {"api", "worker"}:
            raise ValueError("DEPLOYFORGE_RUNTIME_ROLE must be 'api' or 'worker'")
        return normalized

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        value = value.strip()
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value.removeprefix("postgresql://")
        if not value:
            raise ValueError("DEPLOYFORGE_DATABASE_URL cannot be empty")
        return value

    @field_validator("database_sslmode", mode="before")
    @classmethod
    def normalize_database_sslmode(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        supported = {
            "",
            "disable",
            "allow",
            "prefer",
            "require",
            "verify-ca",
            "verify-full",
        }
        if normalized not in supported:
            raise ValueError(
                "DEPLOYFORGE_DATABASE_SSLMODE must be a supported PostgreSQL sslmode"
            )
        return normalized

    @field_validator("database_sslrootcert", mode="before")
    @classmethod
    def normalize_database_sslrootcert(cls, value: str) -> str:
        return str(value or "").strip()

    @property
    def database_connect_args(self) -> dict[str, str]:
        args: dict[str, str] = {}
        if self.database_sslmode:
            args["sslmode"] = self.database_sslmode
        if self.database_sslrootcert:
            args["sslrootcert"] = self.database_sslrootcert
        return args

    @field_validator("session_secret", "oidc_client_secret", mode="before")
    @classmethod
    def normalize_sensitive_settings(cls, value: str | SecretStr | None):
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("scim_bearer_token", mode="before")
    @classmethod
    def normalize_scim_bearer_token(cls, value: str | SecretStr | None):
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("oidc_group_role_mappings")
    @classmethod
    def validate_oidc_group_role_mappings(cls, value: str) -> str:
        try:
            mappings = json.loads(value or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("OIDC group-role mappings must be valid JSON") from exc
        if not isinstance(mappings, dict):
            raise ValueError("OIDC group-role mappings must be a JSON object")
        allowed_roles = {"workspace_admin", "platform_engineer", "service_owner", "auditor"}
        slug_pattern = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?\Z")
        normalized_groups = set()
        for group, workspace_roles in mappings.items():
            if not isinstance(group, str) or not group.strip() or len(group) > 180:
                raise ValueError("OIDC group names must be non-empty strings of at most 180 chars")
            if group.casefold() in normalized_groups:
                raise ValueError("OIDC group names must be unique without regard to case")
            normalized_groups.add(group.casefold())
            if not isinstance(workspace_roles, dict) or not workspace_roles:
                raise ValueError("Each OIDC group must map workspace slugs to roles")
            for workspace_slug, role in workspace_roles.items():
                if not isinstance(workspace_slug, str) or not slug_pattern.fullmatch(
                    workspace_slug
                ):
                    raise ValueError("OIDC group mappings require valid workspace slugs")
                if role not in allowed_roles:
                    raise ValueError("OIDC group mappings contain an unsupported workspace role")
        return json.dumps(mappings, sort_keys=True)

    @field_validator("oidc_group_claim", "oidc_scim_external_id_claim")
    @classmethod
    def validate_oidc_claim_names(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 255:
            raise ValueError("OIDC claim names must contain between 1 and 255 characters")
        return normalized

    @field_validator("session_cookie_name")
    @classmethod
    def validate_session_cookie_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            raise ValueError("DEPLOYFORGE_SESSION_COOKIE_NAME must be a valid cookie token")
        return value

    @field_validator(
        "github_actions_token",
        "github_repository_token",
        "gitlab_build_token",
        "azure_devops_build_token",
        "build_callback_secret",
        "argocd_auth_token",
        "flux_auth_token",
        "session_secret",
        "oidc_client_secret",
        "pagerduty_routing_key",
        mode="before",
    )
    @classmethod
    def blank_secret_is_unset(cls, value: str | SecretStr | None):
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        return value if value and value.strip() else None

    @field_validator("github_actions_workflow_path", "infrastructure_workflow_path")
    @classmethod
    def validate_github_workflow_path(cls, value: str) -> str:
        parts = value.strip().split("/")
        if (
            len(parts) != 3
            or parts[:2] != [".github", "workflows"]
            or not parts[2].endswith((".yml", ".yaml"))
            or any(
                character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
                for character in parts[2]
            )
        ):
            raise ValueError(
                "GitHub Actions workflow path must be a YAML file directly inside .github/workflows"
            )
        return "/".join(parts)

    @field_validator("build_callback_public_url")
    @classmethod
    def validate_build_callback_public_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return ""
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "DEPLOYFORGE_BUILD_CALLBACK_PUBLIC_URL must be a credential-free HTTPS base URL"
            )
        _validate_port(parsed, "DEPLOYFORGE_BUILD_CALLBACK_PUBLIC_URL")
        return value

    @model_validator(mode="after")
    def validate_security_settings(self) -> "Settings":
        environment = self.app_env
        is_production = environment in {"production", "prod"}
        is_development = environment in {"development", "dev", "local"}
        if self.build_callback_secret and len(self.build_callback_secret.get_secret_value()) < 32:
            raise ValueError(
                "DEPLOYFORGE_BUILD_CALLBACK_SECRET must contain at least 32 characters"
            )
        if self.scim_bearer_token and len(self.scim_bearer_token.get_secret_value()) < 32:
            raise ValueError("DEPLOYFORGE_SCIM_BEARER_TOKEN must contain at least 32 characters")
        parsed_github_api_url = urlsplit(self.github_api_url.strip())
        if (
            parsed_github_api_url.scheme != "https"
            or not parsed_github_api_url.hostname
            or parsed_github_api_url.username
            or parsed_github_api_url.password
            or parsed_github_api_url.query
            or parsed_github_api_url.fragment
        ):
            raise ValueError("DEPLOYFORGE_GITHUB_API_URL must be a credential-free HTTPS base URL")
        _validate_port(parsed_github_api_url, "DEPLOYFORGE_GITHUB_API_URL")
        if bool(self.gitlab_server_url) != bool(self.gitlab_api_url):
            raise ValueError(
                "DEPLOYFORGE_GITLAB_SERVER_URL and DEPLOYFORGE_GITLAB_API_URL must be "
                "configured together"
            )
        if self.gitlab_server_url:
            parsed_gitlab_server_url = urlsplit(self.gitlab_server_url)
            if (
                parsed_gitlab_server_url.scheme != "https"
                or not parsed_gitlab_server_url.hostname
                or parsed_gitlab_server_url.username
                or parsed_gitlab_server_url.password
                or parsed_gitlab_server_url.path not in {"", "/"}
                or parsed_gitlab_server_url.query
                or parsed_gitlab_server_url.fragment
            ):
                raise ValueError(
                    "DEPLOYFORGE_GITLAB_SERVER_URL must be a credential-free HTTPS origin"
                )
            _validate_port(parsed_gitlab_server_url, "DEPLOYFORGE_GITLAB_SERVER_URL")
        if self.gitlab_api_url:
            parsed_gitlab_api_url = urlsplit(self.gitlab_api_url)
            if (
                parsed_gitlab_api_url.scheme != "https"
                or not parsed_gitlab_api_url.hostname
                or parsed_gitlab_api_url.username
                or parsed_gitlab_api_url.password
                or parsed_gitlab_api_url.query
                or parsed_gitlab_api_url.fragment
            ):
                raise ValueError("DEPLOYFORGE_GITLAB_API_URL must be a credential-free HTTPS URL")
            _validate_port(parsed_gitlab_api_url, "DEPLOYFORGE_GITLAB_API_URL")
            parsed_gitlab_server_url = urlsplit(self.gitlab_server_url)
            api_port = parsed_gitlab_api_url.port or 443
            server_port = parsed_gitlab_server_url.port or 443
            if (
                parsed_gitlab_api_url.hostname != parsed_gitlab_server_url.hostname
                or api_port != server_port
            ):
                raise ValueError(
                    "DEPLOYFORGE_GITLAB_API_URL must use the configured GitLab server host"
                )
            if not parsed_gitlab_api_url.path.rstrip("/").endswith("/api/v4"):
                raise ValueError("DEPLOYFORGE_GITLAB_API_URL must end with /api/v4")
        if self.azure_devops_organization_url:
            parsed_azure_org = urlsplit(self.azure_devops_organization_url)
            org_parts = parsed_azure_org.path.strip("/").split("/")
            if (
                parsed_azure_org.scheme != "https"
                or parsed_azure_org.hostname != "dev.azure.com"
                or parsed_azure_org.username
                or parsed_azure_org.password
                or parsed_azure_org.query
                or parsed_azure_org.fragment
                or len(org_parts) != 1
                or not re.fullmatch(r"[A-Za-z0-9_.-]+", org_parts[0])
            ):
                raise ValueError(
                    "DEPLOYFORGE_AZURE_DEVOPS_ORGANIZATION_URL must be https://dev.azure.com/{org}"
                )
            _validate_port(parsed_azure_org, "DEPLOYFORGE_AZURE_DEVOPS_ORGANIZATION_URL")
        if self.azure_devops_oidc_issuer:
            parsed_azure_issuer = urlsplit(self.azure_devops_oidc_issuer)
            if (
                parsed_azure_issuer.scheme != "https"
                or not parsed_azure_issuer.hostname
                or parsed_azure_issuer.username
                or parsed_azure_issuer.password
                or parsed_azure_issuer.query
                or parsed_azure_issuer.fragment
            ):
                raise ValueError(
                    "DEPLOYFORGE_AZURE_DEVOPS_OIDC_ISSUER must be a credential-free "
                    "HTTPS issuer URL"
                )
            _validate_port(parsed_azure_issuer, "DEPLOYFORGE_AZURE_DEVOPS_OIDC_ISSUER")
        if bool(self.azure_devops_organization_url) != bool(self.azure_devops_oidc_issuer):
            raise ValueError(
                "Azure DevOps organization URL and OIDC issuer must be configured together"
            )
        parsed_github_server_url = urlsplit(self.github_server_url.strip())
        if (
            parsed_github_server_url.scheme != "https"
            or not parsed_github_server_url.hostname
            or parsed_github_server_url.username
            or parsed_github_server_url.password
            or parsed_github_server_url.path not in {"", "/"}
            or parsed_github_server_url.query
            or parsed_github_server_url.fragment
        ):
            raise ValueError(
                "DEPLOYFORGE_GITHUB_SERVER_URL must be a credential-free HTTPS server origin"
            )
        _validate_port(parsed_github_server_url, "DEPLOYFORGE_GITHUB_SERVER_URL")
        parsed_sigstore_issuer = urlsplit(self.sigstore_oidc_issuer.strip())
        if (
            parsed_sigstore_issuer.scheme != "https"
            or not parsed_sigstore_issuer.hostname
            or parsed_sigstore_issuer.username
            or parsed_sigstore_issuer.password
            or parsed_sigstore_issuer.query
            or parsed_sigstore_issuer.fragment
        ):
            raise ValueError(
                "DEPLOYFORGE_SIGSTORE_OIDC_ISSUER must be a credential-free HTTPS issuer URL"
            )
        _validate_port(parsed_sigstore_issuer, "DEPLOYFORGE_SIGSTORE_OIDC_ISSUER")
        if self.sigstore_trusted_root_file and not self.sigstore_trusted_root_sha256:
            raise ValueError(
                "DEPLOYFORGE_SIGSTORE_TRUSTED_ROOT_SHA256 is required with a custom trust root"
            )
        if self.sigstore_trusted_root_sha256 and not self.sigstore_trusted_root_file:
            raise ValueError(
                "DEPLOYFORGE_SIGSTORE_TRUSTED_ROOT_FILE is required with a trust-root checksum"
            )
        if self.sigstore_trusted_root_sha256 and not re.fullmatch(
            r"[0-9a-f]{64}", self.sigstore_trusted_root_sha256
        ):
            raise ValueError(
                "DEPLOYFORGE_SIGSTORE_TRUSTED_ROOT_SHA256 must be a lowercase SHA-256 digest"
            )
        if self.session_ttl_seconds < 300 or self.session_ttl_seconds > 2_592_000:
            raise ValueError("DEPLOYFORGE_SESSION_TTL_SECONDS must be between 300 and 2592000")
        if self.worker_heartbeat_interval_seconds < 1:
            raise ValueError("DEPLOYFORGE_WORKER_HEARTBEAT_INTERVAL_SECONDS must be positive")
        if self.worker_heartbeat_stale_after_seconds < 2 * self.worker_heartbeat_interval_seconds:
            raise ValueError(
                "DEPLOYFORGE_WORKER_HEARTBEAT_STALE_AFTER_SECONDS must be at least "
                "twice the heartbeat interval"
            )
        if self.build_callback_timeout_seconds < 60:
            raise ValueError("DEPLOYFORGE_BUILD_CALLBACK_TIMEOUT_SECONDS must be at least 60")
        if is_production and self.runtime_role == "worker":
            if self.github_actions_token and not self.build_callback_public_url:
                raise ValueError(
                    "The worker requires a public callback URL when GitHub Actions is enabled"
                )
            if self.build_provider == "gitlab_ci" and not (
                self.gitlab_server_url
                and self.gitlab_api_url
                and self.gitlab_build_token
                and self.build_callback_public_url
            ):
                raise ValueError(
                    "Production GitLab CI builds require GitLab URLs, a worker token, "
                    "and a public callback URL"
                )
            if self.build_provider == "azure_devops" and not (
                self.azure_devops_organization_url
                and self.azure_devops_oidc_issuer
                and self.azure_devops_pipeline_map
                and self.azure_devops_build_token
                and self.build_callback_public_url
            ):
                raise ValueError(
                    "Production Azure DevOps builds require organization and OIDC settings, "
                    "pipeline bindings, a worker token, and a public callback URL"
                )
            if (
                self.build_provider == "azure_devops"
                and self.require_verified_supply_chain
                and not self.sigstore_trusted_root_file
            ):
                raise ValueError(
                    "Azure DevOps verified builds require a custom, checksum-pinned "
                    "Sigstore trust root"
                )
        if is_production and self.runtime_role == "api" and self.github_actions_token:
            raise ValueError("The API must not receive the GitHub Actions dispatch token")
        if is_production and self.runtime_role == "api" and self.gitlab_build_token:
            raise ValueError("The API must not receive the GitLab pipeline token")
        if is_production and self.runtime_role == "api" and self.azure_devops_build_token:
            raise ValueError("The API must not receive the Azure DevOps build token")
        if self.gitlab_build_token and len(self.gitlab_build_token.get_secret_value()) < 16:
            raise ValueError("DEPLOYFORGE_GITLAB_BUILD_TOKEN must contain at least 16 characters")
        if (
            self.azure_devops_build_token
            and len(self.azure_devops_build_token.get_secret_value()) < 16
        ):
            raise ValueError(
                "DEPLOYFORGE_AZURE_DEVOPS_BUILD_TOKEN must contain at least 16 characters"
            )
        if self.argocd_api_url:
            parsed_argocd_url = urlsplit(self.argocd_api_url)
            if (
                parsed_argocd_url.scheme != "https"
                or not parsed_argocd_url.hostname
                or parsed_argocd_url.username
                or parsed_argocd_url.password
                or parsed_argocd_url.query
                or parsed_argocd_url.fragment
            ):
                raise ValueError("DEPLOYFORGE_ARGOCD_API_URL must be a credential-free HTTPS URL")
            _validate_port(parsed_argocd_url, "DEPLOYFORGE_ARGOCD_API_URL")
        if self.flux_api_url:
            parsed_flux_url = urlsplit(self.flux_api_url)
            if (
                parsed_flux_url.scheme != "https"
                or not parsed_flux_url.hostname
                or parsed_flux_url.username
                or parsed_flux_url.password
                or parsed_flux_url.path not in {"", "/"}
                or parsed_flux_url.query
                or parsed_flux_url.fragment
            ):
                raise ValueError("DEPLOYFORGE_FLUX_API_URL must be a credential-free HTTPS origin")
            _validate_port(parsed_flux_url, "DEPLOYFORGE_FLUX_API_URL")
        if bool(self.flux_api_url) != bool(self.flux_cluster_name):
            raise ValueError(
                "DEPLOYFORGE_FLUX_API_URL and FLUX_CLUSTER_NAME must be configured together"
            )
        if self.argo_rollouts_api_url:
            parsed_rollouts_url = urlsplit(self.argo_rollouts_api_url)
            if (
                parsed_rollouts_url.scheme != "https"
                or not parsed_rollouts_url.hostname
                or parsed_rollouts_url.username
                or parsed_rollouts_url.password
                or parsed_rollouts_url.query
                or parsed_rollouts_url.fragment
            ):
                raise ValueError(
                    "DEPLOYFORGE_ARGO_ROLLOUTS_API_URL must be a credential-free HTTPS URL"
                )
            _validate_port(parsed_rollouts_url, "DEPLOYFORGE_ARGO_ROLLOUTS_API_URL")
        if (
            is_production
            and self.runtime_role == "worker"
            and bool(self.argocd_api_url) != bool(self.argocd_auth_token)
        ):
            raise ValueError("Argo CD API URL and auth token must be configured together")
        if (
            is_production
            and self.runtime_role == "worker"
            and bool(self.flux_api_url) != bool(self.flux_auth_token)
        ):
            raise ValueError("Flux API URL and auth token must be configured together")
        if is_production and self.runtime_role == "api" and self.flux_auth_token:
            raise ValueError("The API must not receive the Flux Kubernetes API token")
        if self.flux_auth_token and len(self.flux_auth_token.get_secret_value()) < 16:
            raise ValueError("DEPLOYFORGE_FLUX_AUTH_TOKEN must contain at least 16 characters")
        if is_production and self.runtime_role == "api" and self.argocd_auth_token:
            raise ValueError("The API must not receive the Argo CD token")
        if (
            is_production
            and self.runtime_role == "worker"
            and bool(self.argo_rollouts_api_url) != bool(self.argo_rollouts_auth_token)
        ):
            raise ValueError(
                "Argo Rollouts API URL and auth token must be configured together on the worker"
            )
        if is_production and self.runtime_role == "api" and self.argo_rollouts_auth_token:
            raise ValueError("The API must not receive the Argo Rollouts token")
        if (
            self.argo_rollouts_auth_token
            and len(self.argo_rollouts_auth_token.get_secret_value()) < 16
        ):
            raise ValueError("DEPLOYFORGE_ARGO_ROLLOUTS_AUTH_TOKEN must contain at least 16 chars")
        if is_production and self.runtime_role == "api" and self.pagerduty_routing_key:
            raise ValueError("The API must not receive the PagerDuty routing key")
        if self.argocd_api_timeout_seconds < 1:
            raise ValueError("DEPLOYFORGE_ARGOCD_API_TIMEOUT_SECONDS must be positive")
        if self.github_repository_owner and not re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37})", self.github_repository_owner
        ):
            raise ValueError("DEPLOYFORGE_GITHUB_REPOSITORY_OWNER is invalid")
        templates_configured = self.github_repository_provisioning_configured
        if self.github_repository_token and not templates_configured:
            raise ValueError(
                "GitHub repository provisioning requires an owner and at least one path template"
            )
        if (
            is_production
            and self.runtime_role == "worker"
            and templates_configured != bool(self.github_repository_token)
        ):
            raise ValueError(
                "GitHub repository owner/templates and token must be configured together "
                "on the worker"
            )
        if (
            self.runtime_role == "worker"
            and templates_configured
            and (not self.build_callback_secret or not self.build_callback_public_url)
        ):
            raise ValueError(
                "GitHub repository provisioning requires the callback secret and "
                "public URL on the worker"
            )
        if is_production and self.runtime_role == "api" and self.github_repository_token:
            raise ValueError("The API must not receive the GitHub repository provisioning token")
        if (
            self.github_repository_token
            and len(self.github_repository_token.get_secret_value()) < 16
        ):
            raise ValueError("DEPLOYFORGE_GITHUB_REPOSITORY_TOKEN must contain at least 16 chars")
        if self.gitops_sync_timeout_seconds < 60:
            raise ValueError("DEPLOYFORGE_GITOPS_SYNC_TIMEOUT_SECONDS must be at least 60")
        if self.gitops_reconcile_interval_seconds < 1:
            raise ValueError("DEPLOYFORGE_GITOPS_RECONCILE_INTERVAL_SECONDS must be positive")
        if not 7 <= self.telemetry_retention_days <= 365:
            raise ValueError("DEPLOYFORGE_TELEMETRY_RETENTION_DAYS must be between 7 and 365")
        if (
            min(
                self.build_max_critical_vulnerabilities,
                self.build_max_high_vulnerabilities,
            )
            < 0
        ):
            raise ValueError("Vulnerability thresholds must be zero or greater")
        oidc_fields = (
            self.oidc_discovery_url.strip(),
            self.oidc_client_id.strip(),
            self.oidc_client_secret.get_secret_value() if self.oidc_client_secret else "",
        )
        if any(oidc_fields) and not all(oidc_fields):
            raise ValueError(
                "OIDC discovery URL, client ID and client secret must be configured together"
            )

        for setting_name, value in (
            ("DEPLOYFORGE_OIDC_DISCOVERY_URL", self.oidc_discovery_url),
            ("DEPLOYFORGE_OIDC_REDIRECT_URI", self.oidc_redirect_uri),
        ):
            if not value.strip():
                continue
            parsed = urlsplit(value.strip())
            if (
                parsed.scheme not in ({"https"} if is_production else {"http", "https"})
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
            ):
                scheme_description = "HTTPS" if is_production else "an absolute HTTP(S) URL"
                raise ValueError(f"{setting_name} must be {scheme_description}")
            _validate_port(parsed, setting_name)
            if setting_name.endswith("REDIRECT_URI") and parsed.query:
                raise ValueError(f"{setting_name} cannot contain a query string")

        frontend_url = urlsplit(self.frontend_base_url.strip())
        if (
            frontend_url.scheme not in ({"https"} if is_production else {"http", "https"})
            or not frontend_url.hostname
            or frontend_url.username
            or frontend_url.password
            or frontend_url.query
            or frontend_url.fragment
            or frontend_url.path not in ("", "/")
        ):
            scheme_description = "an HTTPS origin" if is_production else "an HTTP(S) origin"
            raise ValueError(f"DEPLOYFORGE_FRONTEND_BASE_URL must be {scheme_description}")
        _validate_port(frontend_url, "DEPLOYFORGE_FRONTEND_BASE_URL")

        if self.dev_auth_enabled or self.dev_auth_auto_admin_first_user:
            if not is_development:
                raise ValueError("Development authentication may only be enabled locally")

        if is_production:
            database = urlsplit(self.database_url)
            if (
                database.scheme != "postgresql+psycopg"
                or not database.hostname
                or not _port_is_valid(database)
            ):
                raise ValueError(
                    "Production requires a PostgreSQL DEPLOYFORGE_DATABASE_URL using "
                    "the psycopg driver"
                )
        if self.database_require_ssl:
            url_query = parse_qs(urlsplit(self.database_url).query)
            sslmode = self.database_sslmode or next(
                iter(url_query.get("sslmode", [])), ""
            )
            if sslmode not in {"require", "verify-ca", "verify-full"}:
                raise ValueError(
                    "DEPLOYFORGE_DATABASE_REQUIRE_SSL requires sslmode=require, "
                    "verify-ca, or verify-full"
                )
            root_cert = self.database_sslrootcert or next(
                iter(url_query.get("sslrootcert", [])), ""
            )
            if sslmode in {"verify-ca", "verify-full"} and not root_cert:
                raise ValueError(
                    "DEPLOYFORGE_DATABASE_SSLROOTCERT is required for verified PostgreSQL TLS"
                )

        if is_production and self.runtime_role == "api":
            if self.session_secret is None or len(self.session_secret.get_secret_value()) < 32:
                raise ValueError(
                    "DEPLOYFORGE_SESSION_SECRET must contain at least 32 characters in production"
                )
            if not self.session_cookie_secure:
                raise ValueError("DEPLOYFORGE_SESSION_COOKIE_SECURE must be true in production")
            if self.dev_auth_enabled or self.dev_auth_auto_admin_first_user:
                raise ValueError(
                    "Development authentication settings cannot be enabled in production"
                )
            if not self.oidc_configured:
                raise ValueError(
                    "Production requires a complete OIDC identity provider configuration"
                )
            if not self.oidc_redirect_uri.strip():
                raise ValueError("Production requires an explicit HTTPS OIDC redirect URI")
            if not self.build_callback_secret:
                raise ValueError("Production API requires DEPLOYFORGE_BUILD_CALLBACK_SECRET")
            if not self.require_verified_supply_chain:
                raise ValueError(
                    "Production API requires DEPLOYFORGE_REQUIRE_VERIFIED_SUPPLY_CHAIN=true"
                )
        if is_production and self.runtime_role == "worker":
            if self.session_secret or self.oidc_client_secret:
                raise ValueError("The production worker must not receive session or OIDC secrets")
        if self.dev_auth_auto_admin_first_user and not self.dev_auth_enabled:
            raise ValueError(
                "First-user development admin bootstrap requires development authentication"
            )
        return self

    @property
    def oidc_configured(self) -> bool:
        return bool(
            self.oidc_discovery_url
            and self.oidc_client_id
            and self.oidc_client_secret
            and self.oidc_client_secret.get_secret_value()
        )

    @property
    def verified_approval_identity_required(self) -> bool:
        return self.app_env in {"production", "prod"} or self.require_verified_approval_identity

    @property
    def oidc_admin_email_set(self) -> set[str]:
        return {
            email.strip().casefold() for email in self.oidc_admin_emails.split(",") if email.strip()
        }

    @property
    def oidc_group_role_map(self) -> dict[str, dict[str, str]]:
        mappings = json.loads(self.oidc_group_role_mappings or "{}")
        return {
            group.casefold(): {slug: role for slug, role in workspace_roles.items()}
            for group, workspace_roles in mappings.items()
        }

    @property
    def github_actions_builds_configured(self) -> bool:
        return bool(
            self.github_actions_token
            and self.github_actions_token.get_secret_value().strip()
            and self.build_callback_public_url
        )

    @property
    def selected_build_provider_configured(self) -> bool:
        return self.build_provider_configured_for(self.build_provider)

    def build_provider_configured_for(self, provider: str) -> bool:
        if provider == "gitlab_ci":
            return bool(
                self.gitlab_server_url
                and self.gitlab_api_url
                and self.gitlab_build_token
                and self.gitlab_build_token.get_secret_value().strip()
                and self.build_callback_public_url
            )
        if provider == "azure_devops":
            return bool(
                self.azure_devops_organization_url
                and self.azure_devops_oidc_issuer
                and self.azure_devops_pipeline_map
                and self.azure_devops_build_token
                and self.azure_devops_build_token.get_secret_value().strip()
                and self.build_callback_public_url
            )
        if provider == "github_actions":
            return self.github_actions_builds_configured
        return provider == "artifact_promotion"

    @property
    def selected_build_provider_available(self) -> bool:
        """Public configuration visible to the API; provider credentials stay in workers."""
        return self.build_provider_available_for(self.build_provider)

    def build_provider_available_for(self, provider: str) -> bool:
        if not self.build_callback_public_url:
            return False
        if provider == "github_actions":
            return True
        if provider == "gitlab_ci":
            return bool(self.gitlab_server_url and self.gitlab_api_url)
        if provider == "azure_devops":
            return bool(
                self.azure_devops_organization_url
                and self.azure_devops_oidc_issuer
                and self.azure_devops_pipeline_map
            )
        return provider == "artifact_promotion"

    @property
    def azure_devops_pipeline_map(self) -> dict[str, dict[str, object]]:
        return {
            repository.casefold(): binding
            for repository, binding in json.loads(self.azure_devops_pipeline_ids or "{}").items()
        }

    @property
    def github_repository_template_map(self) -> dict[str, str]:
        templates = json.loads(self.github_repository_templates or "{}")
        return {slug: repository for slug, repository in templates.items()}

    @property
    def github_repository_provisioning_configured(self) -> bool:
        return bool(self.github_repository_owner and self.github_repository_template_map)

    def github_template_for_path(self, golden_path_slug: str | None) -> tuple[str, str] | None:
        if not golden_path_slug:
            return None
        repository = self.github_repository_template_map.get(golden_path_slug)
        if not repository:
            return None
        owner, name = repository.split("/", 1)
        return owner, name

    @property
    def infrastructure_runs_configured(self) -> bool:
        return bool(
            self.github_actions_token
            and self.github_actions_token.get_secret_value().strip()
            and self.build_callback_public_url
        )

    @property
    def infrastructure_runs_available(self) -> bool:
        """Public API configuration; dispatch credentials stay in workers."""
        return bool(self.build_callback_secret and self.build_callback_public_url)

    @property
    def argocd_configured(self) -> bool:
        return bool(
            self.argocd_api_url
            and self.argocd_auth_token
            and self.argocd_auth_token.get_secret_value().strip()
        )

    @property
    def argocd_available(self) -> bool:
        """Public API configuration; the Argo CD credential stays in workers."""
        return bool(self.argocd_api_url)

    @property
    def flux_configured(self) -> bool:
        return bool(
            self.flux_api_url
            and self.flux_cluster_name
            and self.flux_auth_token
            and self.flux_auth_token.get_secret_value().strip()
        )

    @property
    def flux_available(self) -> bool:
        """Public connection configuration; the Kubernetes credential stays in workers."""
        return bool(self.flux_api_url and self.flux_cluster_name)

    @property
    def argo_rollouts_configured(self) -> bool:
        return bool(self.argo_rollouts_api_url)

    @property
    def argo_rollouts_credentials_configured(self) -> bool:
        return bool(
            self.argo_rollouts_api_url
            and self.argo_rollouts_auth_token
            and self.argo_rollouts_auth_token.get_secret_value().strip()
        )

    @property
    def argo_rollouts_available(self) -> bool:
        """Public API configuration; the Rollouts credential stays in workers."""
        return bool(self.argo_rollouts_api_url)

    @property
    def pagerduty_configured(self) -> bool:
        return bool(
            self.pagerduty_routing_key and self.pagerduty_routing_key.get_secret_value().strip()
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="DEPLOYFORGE_",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
