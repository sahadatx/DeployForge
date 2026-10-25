import os
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
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
    worker_heartbeat_interval_seconds: int = 10
    worker_heartbeat_stale_after_seconds: int = 30
    telemetry_retention_days: int = 31
    github_actions_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"
    github_actions_workflow_path: str = ".github/workflows/deployforge-build.yml"
    infrastructure_workflow_path: str = ".github/workflows/deployforge-infrastructure.yml"
    build_callback_secret: SecretStr | None = None
    build_callback_public_url: str = ""
    build_callback_timeout_seconds: int = 14_400
    build_max_critical_vulnerabilities: int = 0
    build_max_high_vulnerabilities: int = 0
    require_verified_supply_chain: bool = False
    argocd_api_url: str = ""
    argocd_auth_token: SecretStr | None = None
    argocd_api_timeout_seconds: int = 15
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
    frontend_base_url: str = "http://localhost:5173"
    dev_auth_enabled: bool = False
    dev_auth_auto_admin_first_user: bool = False

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
            "build_callback_secret",
            "argocd_auth_token",
            "session_secret",
            "oidc_client_secret",
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
                resolved[field_name] = (
                    Path(secret_file).read_text(encoding="utf-8").rstrip("\r\n")
                )
            except OSError as exc:
                raise ValueError(f"Could not read {file_environment_name}") from exc
        return resolved

    @field_validator(
        "github_api_url",
        "argocd_api_url",
        "oidc_discovery_url",
        "oidc_redirect_uri",
        "frontend_base_url",
        mode="before",
    )
    @classmethod
    def trim_configured_urls(cls, value: str) -> str:
        return str(value).strip()

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

    @field_validator("session_secret", "oidc_client_secret", mode="before")
    @classmethod
    def normalize_sensitive_settings(cls, value: str | SecretStr | None):
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("session_cookie_name")
    @classmethod
    def validate_session_cookie_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            raise ValueError("DEPLOYFORGE_SESSION_COOKIE_NAME must be a valid cookie token")
        return value

    @field_validator(
        "github_actions_token",
        "build_callback_secret",
        "argocd_auth_token",
        "session_secret",
        "oidc_client_secret",
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
        parsed_github_api_url = urlsplit(self.github_api_url.strip())
        if (
            parsed_github_api_url.scheme != "https"
            or not parsed_github_api_url.hostname
            or parsed_github_api_url.username
            or parsed_github_api_url.password
            or parsed_github_api_url.query
            or parsed_github_api_url.fragment
        ):
            raise ValueError(
                "DEPLOYFORGE_GITHUB_API_URL must be a credential-free HTTPS base URL"
            )
        _validate_port(parsed_github_api_url, "DEPLOYFORGE_GITHUB_API_URL")
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
        if is_production and self.runtime_role == "api" and self.github_actions_token:
            raise ValueError("The API must not receive the GitHub Actions dispatch token")
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
        if (
            is_production
            and self.runtime_role == "worker"
            and bool(self.argocd_api_url) != bool(self.argocd_auth_token)
        ):
            raise ValueError("Argo CD API URL and auth token must be configured together")
        if is_production and self.runtime_role == "api" and self.argocd_auth_token:
            raise ValueError("The API must not receive the Argo CD token")
        if self.argocd_api_timeout_seconds < 1:
            raise ValueError("DEPLOYFORGE_ARGOCD_API_TIMEOUT_SECONDS must be positive")
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

        if is_production and self.runtime_role == "api":
            if (
                self.session_secret is None
                or len(self.session_secret.get_secret_value()) < 32
            ):
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
    def oidc_admin_email_set(self) -> set[str]:
        return {
            email.strip().casefold() for email in self.oidc_admin_emails.split(",") if email.strip()
        }

    @property
    def github_actions_builds_configured(self) -> bool:
        return bool(
            self.github_actions_token
            and self.github_actions_token.get_secret_value().strip()
            and self.build_callback_public_url
        )

    @property
    def infrastructure_runs_configured(self) -> bool:
        return bool(
            self.github_actions_token
            and self.github_actions_token.get_secret_value().strip()
            and self.build_callback_secret
            and self.build_callback_secret.get_secret_value().strip()
            and self.build_callback_public_url
        )

    @property
    def argocd_configured(self) -> bool:
        return bool(
            self.argocd_api_url
            and self.argocd_auth_token
            and self.argocd_auth_token.get_secret_value().strip()
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
