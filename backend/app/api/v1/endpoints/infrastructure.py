from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_identity, get_db, require_csrf
from app.core.audit import record_audit
from app.core.authorization import require_workspace_access
from app.core.builds import verify_callback_signature
from app.core.config import settings
from app.core.infrastructure import (
    InfrastructureTransitionConflict,
    apply_infrastructure_callback,
    create_infrastructure_run,
    transition_infrastructure_run,
)
from app.integrations.github_actions import GitHubDispatchError, parse_github_repository
from app.models import (
    Environment,
    InfrastructureRun,
    InfrastructureRunEvent,
    InfrastructureTarget,
    User,
    UserSession,
)
from app.schemas.core import (
    InfrastructureDecision,
    InfrastructurePlanCallback,
    InfrastructureRunContract,
    InfrastructureRunCreate,
    InfrastructureRunDetail,
    InfrastructureTargetContract,
    InfrastructureTargetCreate,
    InfrastructureTargetPatch,
)

router = APIRouter()
INFRA_OPERATOR_ROLES = {"workspace_admin", "platform_engineer"}
MAX_CALLBACK_BYTES = 256 * 1024


def _load_target(db: Session, workspace_id: UUID, target_id: UUID) -> InfrastructureTarget:
    target = db.scalar(
        select(InfrastructureTarget).where(
            InfrastructureTarget.workspace_id == workspace_id,
            InfrastructureTarget.id == target_id,
        )
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Infrastructure target not found")
    return target


def _load_run(
    db: Session, workspace_id: UUID, run_id: UUID, *, detail: bool = False
) -> InfrastructureRun:
    query = select(InfrastructureRun).where(
        InfrastructureRun.workspace_id == workspace_id,
        InfrastructureRun.id == run_id,
    )
    if detail:
        query = query.options(selectinload(InfrastructureRun.events))
    run = db.scalar(query)
    if run is None:
        raise HTTPException(status_code=404, detail="Infrastructure run not found")
    return run


@router.get(
    "/workspaces/{workspace_id}/infrastructure-targets",
    response_model=list[InfrastructureTargetContract],
    summary="List infrastructure automation targets",
)
def list_infrastructure_targets(
    workspace_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[InfrastructureTarget]:
    require_workspace_access(db, identity[0], workspace_id)
    return list(
        db.scalars(
            select(InfrastructureTarget)
            .where(InfrastructureTarget.workspace_id == workspace_id)
            .order_by(InfrastructureTarget.environment_id)
        ).all()
    )


@router.post(
    "/workspaces/{workspace_id}/infrastructure-targets",
    response_model=InfrastructureTargetContract,
    status_code=status.HTTP_201_CREATED,
    summary="Register Terraform or OpenTofu automation for an environment",
)
def create_infrastructure_target(
    workspace_id: UUID,
    payload: InfrastructureTargetCreate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> InfrastructureTarget:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=INFRA_OPERATOR_ROLES)
    environment = db.scalar(
        select(Environment).where(
            Environment.workspace_id == workspace_id,
            Environment.id == payload.environment_id,
        )
    )
    if environment is None:
        raise HTTPException(status_code=422, detail="Environment must belong to this workspace")
    try:
        parse_github_repository(payload.repository_url)
    except GitHubDispatchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    target = InfrastructureTarget(
        workspace_id=workspace_id,
        environment_id=environment.id,
        repository_url=payload.repository_url,
        source_ref=payload.source_ref,
        working_directory=payload.working_directory,
        tool=payload.tool,
        backend_workspace=payload.backend_workspace.strip() if payload.backend_workspace else None,
        enabled=payload.enabled,
        created_by=actor.id,
    )
    db.add(target)
    try:
        db.flush()
        record_audit(
            db,
            actor=actor,
            action="infrastructure_target.created",
            resource_type="infrastructure_target",
            resource_id=target.id,
            workspace_id=workspace_id,
            details={
                "environment_id": str(environment.id),
                "tool": target.tool,
                "repository_url": target.repository_url,
                "source_ref": target.source_ref,
                "working_directory": target.working_directory,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="An infrastructure target already exists for this environment"
        ) from exc
    db.refresh(target)
    return target


@router.patch(
    "/workspaces/{workspace_id}/infrastructure-targets/{target_id}",
    response_model=InfrastructureTargetContract,
    summary="Update or disable an infrastructure target",
)
def patch_infrastructure_target(
    workspace_id: UUID,
    target_id: UUID,
    payload: InfrastructureTargetPatch,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> InfrastructureTarget:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=INFRA_OPERATOR_ROLES)
    target = _load_target(db, workspace_id, target_id)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Provide at least one target setting")
    candidate = {
        "environment_id": target.environment_id,
        "repository_url": target.repository_url,
        "source_ref": target.source_ref,
        "working_directory": target.working_directory,
        "tool": target.tool,
        "backend_workspace": target.backend_workspace,
        "enabled": target.enabled,
        **updates,
    }
    try:
        validated = InfrastructureTargetCreate.model_validate(candidate)
        parse_github_repository(validated.repository_url)
    except (ValidationError, GitHubDispatchError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for field in updates:
        setattr(target, field, getattr(validated, field))
    record_audit(
        db,
        actor=actor,
        action="infrastructure_target.updated",
        resource_type="infrastructure_target",
        resource_id=target.id,
        workspace_id=workspace_id,
        details={"updated_fields": sorted(updates)},
    )
    db.commit()
    db.refresh(target)
    return target


@router.post(
    "/workspaces/{workspace_id}/environments/{environment_id}/infrastructure-runs",
    response_model=InfrastructureRunContract,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a durable infrastructure plan or drift check",
)
def create_infrastructure_run_endpoint(
    workspace_id: UUID,
    environment_id: UUID,
    payload: InfrastructureRunCreate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> InfrastructureRun:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=INFRA_OPERATOR_ROLES)
    environment = db.scalar(
        select(Environment).where(
            Environment.workspace_id == workspace_id,
            Environment.id == environment_id,
        )
    )
    if environment is None:
        raise HTTPException(status_code=404, detail="Environment not found")
    target = db.scalar(
        select(InfrastructureTarget).where(
            InfrastructureTarget.workspace_id == workspace_id,
            InfrastructureTarget.environment_id == environment_id,
        )
    )
    if target is None or not target.enabled:
        raise HTTPException(status_code=409, detail="An enabled infrastructure target is required")
    try:
        run = create_infrastructure_run(
            db,
            environment,
            target,
            run_kind=payload.run_kind,
            actor=actor,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Another infrastructure operation is active for this environment",
        ) from exc
    db.refresh(run)
    return run


@router.get(
    "/workspaces/{workspace_id}/infrastructure-runs",
    response_model=list[InfrastructureRunContract],
    summary="List infrastructure plans, applies, and drift checks",
)
def list_infrastructure_runs(
    workspace_id: UUID,
    environment_id: UUID | None = None,
    state: str | None = Query(default=None, alias="status", max_length=40),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[InfrastructureRun]:
    require_workspace_access(db, identity[0], workspace_id)
    query = select(InfrastructureRun).where(InfrastructureRun.workspace_id == workspace_id)
    if environment_id:
        query = query.where(InfrastructureRun.environment_id == environment_id)
    if state:
        query = query.where(InfrastructureRun.status == state.casefold())
    return list(
        db.scalars(
            query.order_by(InfrastructureRun.created_at.desc(), InfrastructureRun.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )


@router.get(
    "/workspaces/{workspace_id}/infrastructure-runs/{run_id}",
    response_model=InfrastructureRunDetail,
    summary="Read infrastructure plan evidence and transition history",
)
def get_infrastructure_run(
    workspace_id: UUID,
    run_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> InfrastructureRun:
    require_workspace_access(db, identity[0], workspace_id)
    return _load_run(db, workspace_id, run_id, detail=True)


@router.post(
    "/workspaces/{workspace_id}/infrastructure-runs/{run_id}/approve",
    response_model=InfrastructureRunContract,
    summary="Approve a saved infrastructure plan and queue its exact apply",
)
def approve_infrastructure_run(
    workspace_id: UUID,
    run_id: UUID,
    payload: InfrastructureDecision,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> InfrastructureRun:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=INFRA_OPERATOR_ROLES)
    run = _load_run(db, workspace_id, run_id)
    if run.status != "awaiting_approval":
        raise HTTPException(status_code=409, detail="Infrastructure plan is not awaiting approval")
    if run.requested_by == actor.id:
        raise HTTPException(
            status_code=403, detail="The plan requester cannot approve their own plan"
        )
    environment = db.get(Environment, run.environment_id)
    if environment is None:
        raise HTTPException(status_code=409, detail="Infrastructure environment no longer exists")
    configured_usage = {
        "cpu_millis": environment.quota_cpu_millis,
        "memory_mib": environment.quota_memory_mib,
        "pods": environment.quota_pods,
        "persistent_volume_claims": environment.quota_persistent_volume_claims,
        "services": environment.quota_services,
    }
    violations = [
        key
        for key, limit in configured_usage.items()
        if limit is not None and run.quota_usage.get(key, limit + 1) > limit
    ]
    if violations:
        raise HTTPException(
            status_code=409,
            detail="Environment quotas changed after planning; queue a fresh plan before applying",
        )
    if environment.protected and not environment.allow_destroy and (run.resource_destroys or 0) > 0:
        raise HTTPException(
            status_code=409,
            detail="Protected environment policy blocks resource destruction",
        )
    try:
        transition_infrastructure_run(
            db,
            run,
            "apply_queued",
            event_type="plan.approved",
            message="Authorized operator approved the saved infrastructure plan for apply.",
            actor=actor,
            details={"comment": payload.comment, "plan_digest": run.plan_digest},
            approved_by=actor.id,
            active_operation="apply",
        )
        db.commit()
    except InfrastructureTransitionConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.refresh(run)
    return run


@router.post(
    "/workspaces/{workspace_id}/infrastructure-runs/{run_id}/reject",
    response_model=InfrastructureRunContract,
    summary="Reject a saved infrastructure plan",
)
def reject_infrastructure_run(
    workspace_id: UUID,
    run_id: UUID,
    payload: InfrastructureDecision,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> InfrastructureRun:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=INFRA_OPERATOR_ROLES)
    run = _load_run(db, workspace_id, run_id)
    if run.status != "awaiting_approval":
        raise HTTPException(status_code=409, detail="Infrastructure plan is not awaiting approval")
    if run.requested_by == actor.id:
        raise HTTPException(
            status_code=403, detail="The plan requester cannot reject their own plan"
        )
    try:
        transition_infrastructure_run(
            db,
            run,
            "rejected",
            event_type="plan.rejected",
            message="Authorized operator rejected the saved infrastructure plan.",
            actor=actor,
            details={"comment": payload.comment, "plan_digest": run.plan_digest},
        )
        db.commit()
    except InfrastructureTransitionConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.refresh(run)
    return run


@router.post(
    "/workspaces/{workspace_id}/infrastructure-runs/{run_id}/retry",
    response_model=InfrastructureRunContract,
    summary="Requeue a blocked infrastructure run after correcting its configuration",
)
def retry_infrastructure_run(
    workspace_id: UUID,
    run_id: UUID,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> InfrastructureRun:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=INFRA_OPERATOR_ROLES)
    run = _load_run(db, workspace_id, run_id)
    if run.status != "blocked":
        raise HTTPException(
            status_code=409, detail="Only blocked infrastructure runs can be retried"
        )
    if not settings.infrastructure_runs_configured:
        raise HTTPException(
            status_code=409, detail="Infrastructure workflow integration is not configured"
        )
    target = db.get(InfrastructureTarget, run.target_id) if run.target_id else None
    if target is None or not target.enabled:
        raise HTTPException(
            status_code=409, detail="Enable or register the infrastructure target first"
        )
    try:
        transition_infrastructure_run(
            db,
            run,
            "queued",
            event_type="retry.requested",
            message="Blocked infrastructure run returned to the durable plan queue.",
            actor=actor,
            details={"previous_failure_code": run.failure_code},
            active_operation="plan",
            plan_digest=None,
            plan_artifact_uri=None,
            plan_has_changes=None,
            resource_additions=None,
            resource_changes=None,
            resource_destroys=None,
            quota_usage={},
            approved_by=None,
            attempt_number=run.attempt_number + 1,
        )
        db.commit()
    except (InfrastructureTransitionConflict, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Infrastructure run could not be requeued"
        ) from exc
    db.refresh(run)
    return run


@router.post(
    "/infrastructure-runs/{run_id}/callback",
    response_model=InfrastructureRunContract,
    summary="Receive a signed Terraform/OpenTofu workflow result",
)
async def receive_infrastructure_callback(
    run_id: UUID,
    request: Request,
    x_deployforge_timestamp: str = Header(default="", alias="X-DeployForge-Timestamp"),
    x_deployforge_signature: str = Header(default="", alias="X-DeployForge-Signature"),
    db: Session = Depends(get_db),
) -> InfrastructureRun:
    callback_secret = settings.build_callback_secret
    if callback_secret is None:
        raise HTTPException(status_code=503, detail="Infrastructure callback is not configured")
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
            status_code=401, detail="Infrastructure callback signature is invalid or expired"
        )
    try:
        payload = InfrastructurePlanCallback.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail="Infrastructure callback payload is invalid"
        ) from exc
    run = db.get(InfrastructureRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Infrastructure run not found")
    try:
        apply_infrastructure_callback(db, run, payload)
        db.commit()
    except InfrastructureTransitionConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        duplicate = db.scalar(
            select(InfrastructureRunEvent).where(
                InfrastructureRunEvent.infrastructure_run_id == run_id,
                InfrastructureRunEvent.delivery_id == payload.delivery_id,
            )
        )
        if duplicate is None:
            raise HTTPException(
                status_code=409, detail="Infrastructure callback conflicts with run history"
            ) from exc
        run = db.get(InfrastructureRun, run_id)
    db.refresh(run)
    return run
