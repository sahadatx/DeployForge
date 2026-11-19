"""Operator-only local account provisioning: python -m app.local_accounts --help."""

import argparse
import getpass
import sys

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.core.local_passwords import LOCAL_ISSUER, hash_password
from app.db.session import SessionLocal
from app.models import User, UserSession


def set_local_password(
    db: Session,
    *,
    email: str,
    password: str,
    create: bool = False,
    display_name: str | None = None,
) -> User:
    """Require operator DB access; never expose this function as an HTTP route."""
    if settings.app_env not in {"development", "dev", "local"} or not settings.dev_auth_enabled:
        raise ValueError("Local accounts can only be provisioned when development auth is enabled")
    email = str(TypeAdapter(EmailStr).validate_python(email.strip())).casefold()
    name = display_name.strip() if display_name else email.split("@", 1)[0]
    if not 1 <= len(name) <= 180:
        raise ValueError("Display name must contain 1–180 characters")
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is not None and user.issuer != LOCAL_ISSUER:
        raise ValueError("This email belongs to an organization identity; use organization SSO")
    if user is not None and not user.active:
        raise ValueError("The local account is disabled")
    if user is None and not create:
        raise ValueError("Local account not found; use --create to provision a new account")
    encoded = hash_password(password)
    creating = user is None
    if user is None:
        first_user = (db.scalar(select(func.count()).select_from(User)) or 0) == 0
        user = User(
            issuer=LOCAL_ISSUER,
            subject=email,
            email=email,
            display_name=name,
            email_verified=False,
            is_platform_admin=first_user and settings.dev_auth_auto_admin_first_user,
            platform_admin_source=(
                "manual" if first_user and settings.dev_auth_auto_admin_first_user else "none"
            ),
        )
        db.add(user)
        db.flush()
        if user.is_platform_admin:
            record_audit(
                db,
                actor=user,
                action="identity.platform_admin.bootstrapped",
                resource_type="user",
                resource_id=user.id,
                details={"method": "first_development_user_operator_provisioning"},
            )
    user.password_hash = encoded
    user.email_verified = False
    # Existing identities and workspace memberships remain intact. Password
    # resets revoke all sessions so a lost password cannot leave access behind.
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    record_audit(
        db,
        action=(
            "identity.local_account.provisioned" if creating else "identity.local_password.reset"
        ),
        resource_type="user",
        resource_id=user.id,
        details={"method": "operator_cli", "sessions_revoked": True},
    )
    db.commit()
    return user


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision development account passwords locally")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("set-password", help="Set or reset a local account password")
    command.add_argument("--email", required=True)
    command.add_argument("--create", action="store_true", help="Allow a new local account")
    command.add_argument("--display-name", help="Display name for a new account")
    args = parser.parse_args(argv)
    try:
        if settings.app_env not in {"development", "dev", "local"} or not settings.dev_auth_enabled:
            raise ValueError(
                "Local accounts can only be provisioned when development auth is enabled"
            )
        password = getpass.getpass("Password (12–128 characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise ValueError("Passwords do not match")
        with SessionLocal() as db:
            user = set_local_password(
                db,
                email=args.email,
                password=password,
                create=args.create,
                display_name=args.display_name,
            )
            print(f"Password saved for {user.email}. Existing sessions have been revoked.")
        return 0
    except (ValueError, ValidationError, EOFError, KeyboardInterrupt) as exc:
        print(str(exc) or "Password setup cancelled", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
