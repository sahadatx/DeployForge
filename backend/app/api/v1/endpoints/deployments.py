from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_identity, get_db, require_csrf
from app.core.authorization import require_workspace_access
from app.core.builds import create_build_run, transition_build_run
from app.core.deployments import (
    DeploymentTransitionConflict,
    append_deployment_event,
    transition_deployment,
)
from app.core.gitops import create_gitops_run, transition_gitops_run
from app.models import BuildRun, Deployment, Environment, GitOpsRun, Service, User, UserSession
from app.schemas.core import (
    DeploymentApprovalRequest,
    DeploymentContract,
    DeploymentEventContract,
    DeploymentRequest,
    DeploymentRetryResponse,
)

router = APIRouter()
DEPLOYER_ROLES = {"workspace_admin", "platform_engineer"}
APPROVER_ROLES = {"workspace_admin", "platform_engineer"}


def _load_deployment(db: Session, workspace_id: UUID, deployment_id: UUID) -> Deployment:
    deployment = db.scalar(
        select(Deployment)
        .options(selectinload(Deployment.events))
        .where(Deployment.workspace_id == workspace_id, Deployment.id == deployment_id)
    )
    if deployment is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return deployment


@router.get(
    "/workspaces/{workspace_id}/deployments",
    response_model=list[DeploymentContract],
    summary="List workspace deployment requests",
)
def list_deployments(
    workspace_id: UUID,
    state: str | None = Query(default=None, alias="status", max_length=40),
    service_id: UUID | None = None,
    environment_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[Deployment]:
    require_workspace_access(db, identity[0], workspace_id)
    query = select(Deployment).where(Deployment.workspace_id == workspace_id)
    if state:
        query = query.where(Deployment.status == state.casefold())
    if service_id:
        query = query.where(Deployment.service_id == service_id)
    if environment_id:
        query = query.where(Deployment.environment_id == environment_id)
    return list(
        db.scalars(
            query.order_by(Deployment.created_at.desc(), Deployment.id).limit(limit).offset(offset)
        ).all()
    )


@router.post(
    "/workspaces/{workspace_id}/deployments",
    response_model=DeploymentContract,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a durable deployment request",
)
def request_deployment(
    workspace_id: UUID,
    payload: DeploymentRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Deployment:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=DEPLOYER_ROLES)
    if idempotency_key is not None and not idempotency_key.strip():
        raise HTTPException(status_code=422, detail="Idempotency-Key cannot be blank")
    normalized_key = idempotency_key.strip() if idempotency_key else None
    if normalized_key:
        existing = db.scalar(
            select(Deployment).where(
                Deployment.workspace_id == workspace_id,
                Deployment.idempotency_key == normalized_key,
            )
        )
        if existing is not None:
            if (
                existing.service_id != payload.service_id
                or existing.environment_id != payload.environment_id
                or existing.version != payload.version
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used for a different deployment request",
                )
            response.status_code = status.HTTP_200_OK
            return existing

    service = db.scalar(
        select(Service).where(
            Service.workspace_id == workspace_id, Service.id == payload.service_id
        )
    )
    if service is None:
        raise HTTPException(status_code=422, detail="Service must belong to this workspace")
    if service.lifecycle == "retired":
        raise HTTPException(status_code=409, detail="Retired services cannot be deployed")
    environment = db.scalar(
        select(Environment).where(
            Environment.workspace_id == workspace_id,
            Environment.id == payload.environment_id,
        )
    )
    if environment is None:
        raise HTTPException(status_code=422, detail="Environment must belong to this workspace")

    deployment = Deployment(
        workspace_id=workspace_id,
        service_id=service.id,
        environment_id=environment.id,
        version=payload.version,
        source_ref=payload.source_ref or payload.version,
        status="queued",
        requested_by=str(actor.id),
        idempotency_key=normalized_key,
        attempt_count=0,
        max_attempts=3,
    )
    db.add(deployment)
    try:
        db.flush()
        append_deployment_event(
            db,
            deployment,
            event_type="requested",
            from_status=None,
            to_status="queued",
            message="Deployment request accepted and added to the durable queue.",
            actor=actor,
            details={
                "service_id": str(service.id),
                "environment_id": str(environment.id),
                "version": deployment.version,
                "source_ref": deployment.source_ref,
                "approval_required": True,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if normalized_key:
            existing = db.scalar(
                select(Deployment).where(
                    Deployment.workspace_id == workspace_id,
                    Deployment.idempotency_key == normalized_key,
                )
            )
            if (
                existing
                and existing.service_id == payload.service_id
                and existing.environment_id == payload.environment_id
                and existing.version == payload.version
            ):
                response.status_code = status.HTTP_200_OK
                return existing
        raise HTTPException(
            status_code=409, detail="Deployment request conflicts with existing data"
        ) from exc
    db.refresh(deployment)
    return deployment


@router.get(
    "/workspaces/{workspace_id}/deployments/{deployment_id}",
    response_model=DeploymentContract,
    summary="Read a deployment request",
)
def get_deployment(
    workspace_id: UUID,
    deployment_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> Deployment:
    require_workspace_access(db, identity[0], workspace_id)
    return _load_deployment(db, workspace_id, deployment_id)


@router.get(
    "/workspaces/{workspace_id}/deployments/{deployment_id}/events",
    response_model=list[DeploymentEventContract],
    summary="Read the append-only deployment event history",
)
def list_deployment_events(
    workspace_id: UUID,
    deployment_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list:
    require_workspace_access(db, identity[0], workspace_id)
    deployment = _load_deployment(db, workspace_id, deployment_id)
    return deployment.events


@router.post(
    "/workspaces/{workspace_id}/deployments/{deployment_id}/approval",
    response_model=DeploymentContract,
    summary="Approve or reject a deployment request",
)
def decide_deployment_approval(
    workspace_id: UUID,
    deployment_id: UUID,
    payload: DeploymentApprovalRequest,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Deployment:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=APPROVER_ROLES)
    deployment = _load_deployment(db, workspace_id, deployment_id)
    if deployment.status not in {"awaiting_approval", "awaiting_release_approval"}:
        raise HTTPException(status_code=409, detail="Deployment is not awaiting approval")
    if deployment.requested_by == str(actor.id):
        raise HTTPException(
            status_code=403, detail="The requester cannot approve their own deployment"
        )
    approved = payload.decision == "approve"
    try:
        if deployment.status == "awaiting_release_approval":
            build_run = db.scalar(
                select(BuildRun)
                .where(BuildRun.deployment_id == deployment.id, BuildRun.status == "succeeded")
                .order_by(BuildRun.attempt_number.desc())
            )
            if approved and build_run is None:
                raise HTTPException(
                    status_code=409,
                    detail="Successful build evidence is unavailable for release approval",
                )
            transition_deployment(
                db,
                deployment,
                "ready_to_deploy" if approved else "rejected",
                event_type=(
                    "release_approval.approved" if approved else "release_approval.rejected"
                ),
                message=(
                    "Artifact policy approval granted; release is ready for GitOps deployment."
                    if approved
                    else "Artifact policy approval rejected; release will not be deployed."
                ),
                actor=actor,
                details={
                    "comment": payload.comment.strip() if payload.comment else None,
                    "build_run_id": str(build_run.id) if build_run else None,
                },
            )
            if approved and build_run is not None:
                create_gitops_run(db, deployment, build_run, actor=actor)
            db.commit()
            db.refresh(deployment)
            return deployment

        transition_deployment(
            db,
            deployment,
            "approved" if approved else "rejected",
            event_type=f"approval.{payload.decision}d" if approved else "approval.rejected",
            message=(
                "Deployment approved and ready for the build integration."
                if approved
                else "Deployment rejected by an authorized approver."
            ),
            actor=actor,
            details={"comment": payload.comment.strip() if payload.comment else None},
        )
        if approved:
            service = db.get(Service, deployment.service_id)
            if service is None:
                raise HTTPException(
                    status_code=409, detail="Deployment service is no longer available"
                )
            build_run = create_build_run(db, deployment, service, actor=actor)
            append_deployment_event(
                db,
                deployment,
                event_type="build.queued" if build_run.status == "queued" else "build.blocked",
                from_status="approved",
                to_status="approved",
                message=(
                    "Approved release passed to the CI build queue."
                    if build_run.status == "queued"
                    else build_run.failure_message
                    or "Approved release is blocked by build configuration."
                ),
                actor=actor,
                details={"build_run_id": str(build_run.id), "build_status": build_run.status},
            )
    except DeploymentTransitionConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(deployment)
    return deployment


@router.post(
    "/workspaces/{workspace_id}/deployments/{deployment_id}/retry",
    response_model=DeploymentRetryResponse,
    summary="Retry a failed deployment request",
)
def retry_deployment(
    workspace_id: UUID,
    deployment_id: UUID,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> DeploymentRetryResponse:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=DEPLOYER_ROLES)
    deployment = _load_deployment(db, workspace_id, deployment_id)
    if deployment.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed deployments can be retried")
    if deployment.attempt_count >= deployment.max_attempts:
        raise HTTPException(status_code=409, detail="Deployment attempt limit has been reached")
    try:
        transition_deployment(
            db,
            deployment,
            "queued",
            event_type="retry.requested",
            message="Deployment retry accepted and returned to the durable queue.",
            actor=actor,
            details={"previous_attempts": deployment.attempt_count},
        )
    except DeploymentTransitionConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(deployment)
    return DeploymentRetryResponse(deployment=deployment, message="Deployment queued for retry")


@router.post(
    "/workspaces/{workspace_id}/deployments/{deployment_id}/cancel",
    response_model=DeploymentContract,
    summary="Cancel a deployment before external execution starts",
)
def cancel_deployment(
    workspace_id: UUID,
    deployment_id: UUID,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Deployment:
    actor = identity[0]
    role = require_workspace_access(db, actor, workspace_id)[1]
    deployment = _load_deployment(db, workspace_id, deployment_id)
    if deployment.status not in {
        "queued",
        "awaiting_approval",
        "awaiting_release_approval",
        "approved",
        "ready_to_deploy",
    }:
        raise HTTPException(status_code=409, detail="Deployment can no longer be canceled")
    active_build_run = db.scalar(
        select(BuildRun).where(
            BuildRun.deployment_id == deployment.id,
            BuildRun.status.in_({"dispatching", "dispatched", "running"}),
        )
    )
    if active_build_run is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "The build has already started; cancel it in the CI provider before "
                "changing this release"
            ),
        )
    active_gitops_run = db.scalar(
        select(GitOpsRun).where(
            GitOpsRun.deployment_id == deployment.id,
            GitOpsRun.status.in_({"dispatching", "syncing", "verifying"}),
        )
    )
    if active_gitops_run is not None:
        raise HTTPException(
            status_code=409,
            detail="Argo CD sync started; stop it in Argo CD before changing this deployment.",
        )
    operators = {"platform_admin", "workspace_admin", "platform_engineer"}
    if role not in operators and deployment.requested_by != str(actor.id):
        raise HTTPException(
            status_code=403,
            detail="Only the requester or a platform operator can cancel",
        )
    try:
        transition_deployment(
            db,
            deployment,
            "canceled",
            event_type="canceled",
            message="Deployment canceled before external execution started.",
            actor=actor,
        )
        queued_build_runs = list(
            db.scalars(
                select(BuildRun).where(
                    BuildRun.deployment_id == deployment.id,
                    BuildRun.status.in_({"queued", "blocked"}),
                )
            ).all()
        )
        for build_run in queued_build_runs:
            transition_build_run(
                db,
                build_run,
                "canceled",
                event_type="canceled",
                message="Build canceled because its deployment was canceled.",
                actor=actor,
            )
        queued_gitops_runs = list(
            db.scalars(
                select(GitOpsRun).where(
                    GitOpsRun.deployment_id == deployment.id,
                    GitOpsRun.status.in_({"queued", "blocked"}),
                )
            ).all()
        )
        for gitops_run in queued_gitops_runs:
            transition_gitops_run(
                db,
                gitops_run,
                "canceled",
                event_type="canceled",
                message="GitOps sync canceled because its deployment was canceled.",
                actor=actor,
            )
    except DeploymentTransitionConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(deployment)
    return deployment
