from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_identity, get_db, require_csrf
from app.core.authorization import require_workspace_access
from app.core.builds import (
    BuildRunTransitionConflict,
    apply_build_callback,
    transition_build_run,
    verify_callback_signature,
)
from app.core.config import settings
from app.core.deployments import DeploymentTransitionConflict
from app.core.supply_chain import (
    SupplyChainVerificationRejected,
    SupplyChainVerificationUnavailable,
    verify_callback_evidence,
)
from app.integrations.github_actions import GitHubDispatchError, parse_github_repository
from app.models import BuildRun, BuildRunEvent, User, UserSession
from app.schemas.core import BuildCallbackRequest, BuildRunContract, BuildRunDetail

router = APIRouter()
BUILD_OPERATOR_ROLES = {"workspace_admin", "platform_engineer"}
MAX_CALLBACK_BYTES = 256 * 1024


def _load_build_run(
    db: Session,
    workspace_id: UUID,
    build_run_id: UUID,
    *,
    detail: bool = False,
) -> BuildRun:
    query = select(BuildRun).where(
        BuildRun.workspace_id == workspace_id,
        BuildRun.id == build_run_id,
    )
    if detail:
        query = query.options(
            selectinload(BuildRun.events),
            selectinload(BuildRun.artifacts),
        )
    build_run = db.scalar(query)
    if build_run is None:
        raise HTTPException(status_code=404, detail="Build run not found")
    return build_run


@router.get(
    "/workspaces/{workspace_id}/build-runs",
    response_model=list[BuildRunContract],
    summary="List CI build runs in a workspace",
)
def list_build_runs(
    workspace_id: UUID,
    deployment_id: UUID | None = None,
    state: str | None = Query(default=None, alias="status", max_length=40),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[BuildRun]:
    require_workspace_access(db, identity[0], workspace_id)
    query = select(BuildRun).where(BuildRun.workspace_id == workspace_id)
    if deployment_id:
        query = query.where(BuildRun.deployment_id == deployment_id)
    if state:
        query = query.where(BuildRun.status == state.casefold())
    return list(
        db.scalars(
            query.order_by(BuildRun.created_at.desc(), BuildRun.id).limit(limit).offset(offset)
        ).all()
    )


@router.get(
    "/workspaces/{workspace_id}/build-runs/{build_run_id}",
    response_model=BuildRunDetail,
    summary="Read build logs metadata and immutable supply-chain evidence",
)
def get_build_run(
    workspace_id: UUID,
    build_run_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> BuildRun:
    require_workspace_access(db, identity[0], workspace_id)
    return _load_build_run(db, workspace_id, build_run_id, detail=True)


@router.post(
    "/workspaces/{workspace_id}/build-runs/{build_run_id}/retry",
    response_model=BuildRunContract,
    summary="Retry a blocked build after its provider has been configured",
)
def retry_blocked_build_run(
    workspace_id: UUID,
    build_run_id: UUID,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> BuildRun:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=BUILD_OPERATOR_ROLES)
    build_run = _load_build_run(db, workspace_id, build_run_id)
    if build_run.status != "blocked":
        raise HTTPException(status_code=409, detail="Only blocked build runs can be retried here")
    if not settings.github_actions_builds_configured:
        raise HTTPException(status_code=409, detail="GitHub Actions integration is not configured")
    try:
        parse_github_repository(build_run.repository_url or "")
    except GitHubDispatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        transition_build_run(
            db,
            build_run,
            "queued",
            event_type="retry.requested",
            message="Build run returned to the durable dispatch queue.",
            actor=actor,
            details={"previous_failure_code": build_run.failure_code},
        )
    except BuildRunTransitionConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(build_run)
    return build_run


@router.post(
    "/build-runs/{build_run_id}/callback",
    response_model=BuildRunContract,
    summary="Receive an authenticated CI workflow update and supply-chain evidence",
)
async def receive_build_callback(
    build_run_id: UUID,
    request: Request,
    x_deployforge_timestamp: str = Header(default="", alias="X-DeployForge-Timestamp"),
    x_deployforge_signature: str = Header(default="", alias="X-DeployForge-Signature"),
    db: Session = Depends(get_db),
) -> BuildRun:
    callback_secret = settings.build_callback_secret
    if callback_secret is None:
        raise HTTPException(status_code=503, detail="Build callback integration is not configured")
    body_buffer = bytearray()
    async for chunk in request.stream():
        if len(body_buffer) + len(chunk) > MAX_CALLBACK_BYTES:
            raise HTTPException(status_code=413, detail="Callback payload exceeds the allowed size")
        body_buffer.extend(chunk)
    body = bytes(body_buffer)
    if not verify_callback_signature(
        body,
        timestamp=x_deployforge_timestamp,
        signature=x_deployforge_signature,
        secret=callback_secret.get_secret_value(),
    ):
        raise HTTPException(
            status_code=401, detail="Build callback signature is invalid or expired"
        )
    try:
        payload = BuildCallbackRequest.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Build callback payload is invalid") from exc

    build_run = db.get(BuildRun, build_run_id)
    if build_run is None:
        raise HTTPException(status_code=404, detail="Build run not found")
    evidence_verified = False
    evidence_rejected = False
    if settings.require_verified_supply_chain and payload.status == "succeeded":
        duplicate_delivery = db.scalar(
            select(BuildRunEvent).where(
                BuildRunEvent.build_run_id == build_run.id,
                BuildRunEvent.delivery_id == payload.delivery_id,
            )
        )
        if duplicate_delivery is not None:
            return build_run
        try:
            verify_callback_evidence(payload, build_run)
            evidence_verified = True
        except SupplyChainVerificationRejected:
            evidence_rejected = True
        except SupplyChainVerificationUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Signed evidence verification is temporarily unavailable; "
                    "retry the callback"
                ),
                headers={"Retry-After": "30"},
            ) from exc
    try:
        apply_build_callback(
            db,
            build_run,
            payload,
            evidence_verified=evidence_verified,
            evidence_rejected=evidence_rejected,
        )
        db.commit()
    except BuildRunTransitionConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DeploymentTransitionConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        duplicate = db.scalar(
            select(BuildRunEvent).where(
                BuildRunEvent.build_run_id == build_run_id,
                BuildRunEvent.delivery_id == payload.delivery_id,
            )
        )
        if duplicate is None:
            raise HTTPException(
                status_code=409, detail="Build evidence conflicts with an existing record"
            ) from exc
    db.refresh(build_run)
    return build_run
