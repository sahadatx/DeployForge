"""Synchronize platform-admin grants that originate from the OIDC email allowlist."""

from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.models import User


def sync_oidc_admin_allowlist(db: Session, user: User) -> bool:
    """Apply allowlist grants and revoke removed/legacy production grants."""
    source = user.platform_admin_source
    if source == "manual":
        return False

    production = settings.app_env.casefold() in {"production", "prod"}
    if source not in {"none", "oidc_allowlist"} and not (
        production and source == "legacy"
    ):
        return False

    allowlisted = user.email.casefold() in settings.oidc_admin_email_set
    if allowlisted:
        if user.is_platform_admin and source == "oidc_allowlist":
            return False
        previous_source = source
        user.is_platform_admin = True
        user.platform_admin_source = "oidc_allowlist"
        record_audit(
            db,
            actor=user,
            action="identity.platform_admin.allowlist_granted",
            resource_type="user",
            resource_id=user.id,
            details={"source": "oidc_email_allowlist", "previous_source": previous_source},
        )
        return True

    if user.is_platform_admin and source == "none" and not production:
        user.platform_admin_source = "manual"
        record_audit(
            db,
            actor=user,
            action="identity.platform_admin.manual_source_classified",
            resource_type="user",
            resource_id=user.id,
            details={"previous_source": source, "environment": settings.app_env},
        )
        return True

    if user.is_platform_admin or source in {"oidc_allowlist", "legacy"}:
        was_admin = user.is_platform_admin
        previous_source = source
        user.is_platform_admin = False
        user.platform_admin_source = "none"
        if was_admin:
            record_audit(
                db,
                actor=user,
                action="identity.platform_admin.allowlist_revoked",
                resource_type="user",
                resource_id=user.id,
                details={"source": "oidc_email_allowlist", "previous_source": previous_source},
            )
        return True
    return False
