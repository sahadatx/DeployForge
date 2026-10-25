import pytest
from pydantic import ValidationError

from app.core.config import Settings


def production_settings(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "database_url": "postgresql://deployforge:strong-password@db.example.com:5432/deployforge",
        "session_secret": "s" * 48,
        "session_cookie_secure": True,
        "frontend_base_url": "https://deployforge.example.com",
        "oidc_discovery_url": "https://identity.example.com/.well-known/openid-configuration",
        "oidc_client_id": "deployforge",
        "oidc_client_secret": "provider-secret",
        "oidc_redirect_uri": "https://deployforge.example.com/api/v1/auth/callback",
        "build_callback_secret": "b" * 48,
        "require_verified_supply_chain": True,
        "dev_auth_enabled": False,
        "dev_auth_auto_admin_first_user": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_settings_normalize_postgres_urls_for_psycopg():
    configured = production_settings()

    assert configured.database_url.startswith("postgresql+psycopg://")
    assert "provider-secret" not in repr(configured)
    assert "s" * 48 not in repr(configured)


def test_production_requires_postgres_and_complete_oidc():
    with pytest.raises(ValidationError, match="Production requires a PostgreSQL"):
        production_settings(database_url="sqlite:///./deployforge.db")

    with pytest.raises(ValidationError, match="complete OIDC"):
        production_settings(
            oidc_discovery_url="",
            oidc_client_id="",
            oidc_client_secret="",
        )


def test_production_rejects_insecure_browser_and_identity_urls():
    with pytest.raises(ValidationError, match="FRONTEND_BASE_URL"):
        production_settings(frontend_base_url="http://deployforge.example.com")

    with pytest.raises(ValidationError, match="OIDC_REDIRECT_URI"):
        production_settings(oidc_redirect_uri="http://deployforge.example.com/callback")


def test_development_auth_is_rejected_outside_local_environments():
    with pytest.raises(ValidationError, match="only be enabled locally"):
        Settings(
            _env_file=None,
            app_env="staging",
            dev_auth_enabled=True,
            dev_auth_auto_admin_first_user=True,
        )


def test_partial_oidc_configuration_and_invalid_worker_heartbeat_are_rejected():
    with pytest.raises(ValidationError, match="configured together"):
        Settings(_env_file=None, oidc_client_id="deployforge")

    with pytest.raises(ValidationError, match="at least twice"):
        Settings(
            _env_file=None,
            worker_heartbeat_interval_seconds=15,
            worker_heartbeat_stale_after_seconds=20,
        )


def test_production_rejects_incomplete_release_providers_and_invalid_ui_origins():
    with pytest.raises(ValidationError, match="requires a public callback URL"):
        production_settings(
            runtime_role="worker",
            session_secret=None,
            session_cookie_secure=False,
            oidc_discovery_url="",
            oidc_client_id="",
            oidc_client_secret=None,
            oidc_redirect_uri="",
            build_callback_secret=None,
            require_verified_supply_chain=False,
            github_actions_token="incomplete",
        )

    with pytest.raises(ValidationError, match="invalid port"):
        Settings(_env_file=None, frontend_base_url="http://localhost:not-a-port")

    with pytest.raises(ValidationError, match="cookie token"):
        Settings(_env_file=None, session_cookie_name="session; HttpOnly")
