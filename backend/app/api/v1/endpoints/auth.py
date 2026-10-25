import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_identity, get_db, hash_session_token, require_csrf
from app.core.audit import record_audit
from app.core.config import settings
from app.models import User, UserSession, WorkspaceMembership
from app.schemas.core import AuthSessionContract, UserContract

router = APIRouter()
logger = logging.getLogger("deployforge.auth")
oauth = OAuth()
_oidc_registered = False


class DevelopmentLoginRequest(BaseModel):
    email: EmailStr
    display_name: str | None = Field(default=None, min_length=1, max_length=180)


def _register_oidc_client():
    global _oidc_registered
    if not settings.oidc_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="An OIDC identity provider has not been configured",
        )
    if not _oidc_registered:
        oauth.register(
            name="deployforge",
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret.get_secret_value(),
            server_metadata_url=settings.oidc_discovery_url,
            client_kwargs={
                "scope": "openid profile email",
                "code_challenge_method": "S256",
            },
        )
        _oidc_registered = True
    return oauth.create_client("deployforge")


def _new_server_session(db: Session, user: User) -> tuple[str, UserSession]:
    token = secrets.token_urlsafe(48)
    session = UserSession(
        user_id=user.id,
        token_hash=hash_session_token(token),
        csrf_token=secrets.token_urlsafe(32),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds),
    )
    db.add(session)
    return token, session


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def _frontend_redirect(path: str) -> RedirectResponse:
    base = settings.frontend_base_url.rstrip("/") + "/"
    return RedirectResponse(
        url=urljoin(base, path.lstrip("/")), status_code=status.HTTP_303_SEE_OTHER
    )


def _membership_payload(db: Session, user: User) -> list[dict[str, str]]:
    memberships = db.scalars(
        select(WorkspaceMembership)
        .where(WorkspaceMembership.user_id == user.id)
        .order_by(WorkspaceMembership.created_at)
    ).all()
    return [
        {
            "workspace_id": str(membership.workspace_id),
            "role": membership.role,
        }
        for membership in memberships
    ]


def _finish_login(db: Session, user: User, *, method: str) -> tuple[str, UserSession]:
    token, session = _new_server_session(db, user)
    record_audit(
        db,
        actor=user,
        action="authentication.login.succeeded",
        resource_type="user_session",
        resource_id=session.id,
        outcome="succeeded",
        details={"method": method},
    )
    db.commit()
    return token, session


@router.get("/config", summary="Show available authentication methods")
def auth_config() -> dict[str, bool | str]:
    dev_enabled = (
        settings.app_env.lower() in {"development", "dev", "local"} and settings.dev_auth_enabled
    )
    return {
        "oidc_enabled": settings.oidc_configured,
        "development_login_enabled": dev_enabled,
        "authentication_required": True,
    }


@router.get("/login", name="oidc_login", summary="Start the OpenID Connect login flow")
async def oidc_login(request: Request):
    client = _register_oidc_client()
    callback = settings.oidc_redirect_uri or str(request.url_for("oidc_callback"))
    return await client.authorize_redirect(request, callback)


@router.get("/callback", name="oidc_callback", summary="Complete the OpenID Connect login flow")
async def oidc_callback(request: Request, db: Session = Depends(get_db)):
    try:
        client = _register_oidc_client()
        token = await client.authorize_access_token(request)
        claims = token.get("userinfo")
        if not claims:
            raise ValueError("OIDC user information was not returned")

        subject = str(claims.get("sub", "")).strip()
        issuer = str(claims.get("iss", "")).strip()
        email = str(claims.get("email", "")).strip().casefold()
        email_verified = claims.get("email_verified") is True
        if not subject or not issuer or not email or not email_verified:
            raise ValueError("OIDC identity is missing verified account claims")

        user = db.scalar(select(User).where(User.issuer == issuer, User.subject == subject))
        if user is None:
            email_owner = db.scalar(select(User).where(func.lower(User.email) == email))
            if email_owner is not None:
                record_audit(
                    db,
                    action="authentication.account_link.rejected",
                    resource_type="user",
                    resource_id=email_owner.id,
                    outcome="denied",
                    details={"reason": "existing_email_has_different_identity"},
                )
                db.commit()
                return _frontend_redirect("/login?error=account_link")
            user = User(
                issuer=issuer,
                subject=subject,
                email=email,
                display_name=str(claims.get("name") or email.split("@", 1)[0])[:180],
                email_verified=True,
                is_platform_admin=email in settings.oidc_admin_email_set,
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
                    details={"method": "verified_oidc_email_allowlist"},
                )
        else:
            user.email = email
            user.display_name = str(claims.get("name") or user.display_name)[:180]
            user.email_verified = True
            if email in settings.oidc_admin_email_set:
                user.is_platform_admin = True

        if not user.active:
            record_audit(
                db,
                actor=user,
                action="authentication.login.denied",
                resource_type="user",
                resource_id=user.id,
                outcome="denied",
                details={"reason": "account_inactive"},
            )
            db.commit()
            return _frontend_redirect("/login?error=account_disabled")

        raw_token, _session = _finish_login(db, user, method="oidc")
        response = _frontend_redirect("/overview")
        _set_session_cookie(response, raw_token)
        return response
    except (OAuthError, ValueError, KeyError) as exc:
        logger.info("oidc_authentication_rejected", extra={"reason": type(exc).__name__})
        record_audit(
            db,
            action="authentication.login.failed",
            resource_type="identity_provider",
            outcome="failed",
            details={"method": "oidc", "reason": type(exc).__name__},
        )
        db.commit()
        return _frontend_redirect("/login?error=identity_provider")


@router.post("/dev-login", summary="Create a development-only local session")
def development_login(
    payload: DevelopmentLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthSessionContract:
    dev_enabled = (
        settings.app_env.lower() in {"development", "dev", "local"} and settings.dev_auth_enabled
    )
    if not dev_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != settings.frontend_base_url.rstrip("/"):
        record_audit(
            db,
            action="authentication.dev_login.denied",
            resource_type="identity_provider",
            outcome="denied",
            details={"reason": "origin_validation_failed"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Origin validation failed"
        )

    email = str(payload.email).casefold()
    user = db.scalar(
        select(User).where(User.issuer == "urn:deployforge:development", User.subject == email)
    )
    if user is None:
        existing_count = db.scalar(select(func.count()).select_from(User)) or 0
        auto_admin = settings.dev_auth_auto_admin_first_user and existing_count == 0
        user = User(
            issuer="urn:deployforge:development",
            subject=email,
            email=email,
            display_name=payload.display_name or email.split("@", 1)[0],
            email_verified=True,
            is_platform_admin=auto_admin,
        )
        db.add(user)
        db.flush()
        if auto_admin:
            record_audit(
                db,
                actor=user,
                action="identity.platform_admin.bootstrapped",
                resource_type="user",
                resource_id=user.id,
                details={"method": "first_development_user"},
            )
    elif not user.active:
        record_audit(
            db,
            actor=user,
            action="authentication.login.denied",
            resource_type="user",
            resource_id=user.id,
            outcome="denied",
            details={"method": "development", "reason": "account_inactive"},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is unavailable")
    else:
        user.display_name = payload.display_name or user.display_name

    raw_token, session = _finish_login(db, user, method="development")
    _set_session_cookie(response, raw_token)
    memberships = _membership_payload(db, user)
    return AuthSessionContract(
        user=UserContract.model_validate(user),
        memberships=memberships,
        csrf_token=session.csrf_token,
    )


@router.get(
    "/me", response_model=AuthSessionContract, summary="Return the current user and workspace roles"
)
def current_user(
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> AuthSessionContract:
    user, session = identity
    return AuthSessionContract(
        user=UserContract.model_validate(user),
        memberships=_membership_payload(db, user),
        csrf_token=session.csrf_token,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the current server-side session",
)
def logout(
    response: Response,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Response:
    user, session = identity
    record_audit(
        db,
        actor=user,
        action="authentication.logout",
        resource_type="user_session",
        resource_id=session.id,
    )
    db.delete(session)
    db.commit()
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
