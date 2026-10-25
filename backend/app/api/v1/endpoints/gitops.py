from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_identity, get_db, require_csrf
from app.core.audit import record_audit
from app.core.authorization import require_workspace_access
from app.core.config import settings
from app.core.deployments import DeploymentTransitionConflict
from app.core.gitops import (
    GitOpsTransitionConflict,
    read_target_status,
    request_manual_rollback,
    transition_gitops_run,
)
from app.models import (
    BuildArtifact,
    BuildRun,
    Deployment,
    Environment,
    GitOpsRun,
    GitOpsTarget,
    Service,
    User,
    UserSession,
)
from app.schemas.core import (
    ArgoApplicationStatus,
    GitOpsRunContract,
    GitOpsRunDetail,
    GitOpsTargetContract,
    GitOpsTargetCreate,
    GitOpsTargetPatch,
)

router = APIRouter()
GITOPS_OPERATOR_ROLES = {"workspace_admin", "platform_engineer"}


def _load_target(db: Session, workspace_id: UUID, target_id: UUID) -> GitOpsTarget:
    target = db.scalar(
        select(GitOpsTarget).where(
            GitOpsTarget.workspace_id == workspace_id,
            GitOpsTarget.id == target_id,
        )
    )
    if target is None:
        raise HTTPException(status_code=404, detail="GitOps target not found")
    return target


def _load_run(
    db: Session, workspace_id: UUID, gitops_run_id: UUID, *, detail: bool = False
) -> GitOpsRun:
    query = select(GitOpsRun).where(
        GitOpsRun.workspace_id == workspace_id,
        GitOpsRun.id == gitops_run_id,
    )
    if detail:
        query = query.options(selectinload(GitOpsRun.events))
    run = db.scalar(query)
    if run is None:
        raise HTTPException(status_code=404, detail="GitOps run not found")
    return run


@router.get(
    "/workspaces/{workspace_id}/gitops-targets",
    response_model=list[GitOpsTargetContract],
    summary="List service and environment Argo CD bindings",
)
def list_gitops_targets(
    workspace_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[GitOpsTarget]:
    require_workspace_access(db, identity[0], workspace_id)
    return list(
        db.scalars(
            select(GitOpsTarget)
            .where(GitOpsTarget.workspace_id == workspace_id)
            .order_by(GitOpsTarget.application_name)
        ).all()
    )


@router.post(
    "/workspaces/{workspace_id}/gitops-targets",
    response_model=GitOpsTargetContract,
    status_code=status.HTTP_201_CREATED,
    summary="Register an Argo CD Application target",
)
def create_gitops_target(
    workspace_id: UUID,
    payload: GitOpsTargetCreate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> GitOpsTarget:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=GITOPS_OPERATOR_ROLES)
    service = db.scalar(
        select(Service).where(
            Service.workspace_id == workspace_id,
            Service.id == payload.service_id,
        )
    )
    if service is None:
        raise HTTPException(status_code=422, detail="Service must belong to this workspace")
    environment = db.scalar(
        select(Environment).where(
            Environment.workspace_id == workspace_id,
            Environment.id == payload.environment_id,
        )
    )
    if environment is None:
        raise HTTPException(status_code=422, detail="Environment must belong to this workspace")
    target = GitOpsTarget(
        workspace_id=workspace_id,
        service_id=service.id,
        environment_id=environment.id,
        application_name=payload.application_name,
        expected_project=payload.expected_project.strip(),
        expected_cluster_name=(
            payload.expected_cluster_name.strip() if payload.expected_cluster_name else None
        ),
        artifact_name=payload.artifact_name.strip() if payload.artifact_name else None,
        enabled=payload.enabled,
        created_by=actor.id,
    )
    db.add(target)
    try:
        db.flush()
        record_audit(
            db,
            actor=actor,
            action="gitops_target.created",
            resource_type="gitops_target",
            resource_id=target.id,
            workspace_id=workspace_id,
            details={
                "application_name": target.application_name,
                "service_id": str(service.id),
                "environment_id": str(environment.id),
                "expected_project": target.expected_project,
                "expected_cluster_name": target.expected_cluster_name,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "A GitOps target already exists for this service/environment or application name"
            ),
        ) from exc
    db.refresh(target)
    return target


@router.patch(
    "/workspaces/{workspace_id}/gitops-targets/{target_id}",
    response_model=GitOpsTargetContract,
    summary="Update or disable an Argo CD target binding",
)
def patch_gitops_target(
    workspace_id: UUID,
    target_id: UUID,
    payload: GitOpsTargetPatch,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> GitOpsTarget:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=GITOPS_OPERATOR_ROLES)
    target = _load_target(db, workspace_id, target_id)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Provide at least one GitOps target field")
    for field in ("expected_project", "expected_cluster_name", "artifact_name"):
        if field in updates and isinstance(updates[field], str):
            updates[field] = updates[field].strip() or None
    if "application_name" in updates and updates["application_name"] is None:
        raise HTTPException(status_code=422, detail="Argo CD application name cannot be blank")
    if updates.get("expected_project") is None and "expected_project" in updates:
        raise HTTPException(status_code=422, detail="Argo CD project cannot be blank")
    for key, value in updates.items():
        setattr(target, key, value)
    try:
        record_audit(
            db,
            actor=actor,
            action="gitops_target.updated",
            resource_type="gitops_target",
            resource_id=target.id,
            workspace_id=workspace_id,
            details={"updated_fields": sorted(updates)},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="GitOps target conflicts with an existing binding"
        ) from exc
    db.refresh(target)
    return target


@router.get(
    "/workspaces/{workspace_id}/gitops-targets/{target_id}/status",
    response_model=ArgoApplicationStatus,
    summary="Read Argo CD sync, health, revision, and cluster destination",
)
def get_gitops_target_status(
    workspace_id: UUID,
    target_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> dict:
    require_workspace_access(db, identity[0], workspace_id)
    target = _load_target(db, workspace_id, target_id)
    return read_target_status(target)


@router.get(
    "/workspaces/{workspace_id}/gitops-runs",
    response_model=list[GitOpsRunContract],
    summary="List durable Kubernetes rollout and rollback runs",
)
def list_gitops_runs(
    workspace_id: UUID,
    deployment_id: UUID | None = None,
    state: str | None = Query(default=None, alias="status", max_length=40),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[GitOpsRun]:
    require_workspace_access(db, identity[0], workspace_id)
    query = select(GitOpsRun).where(GitOpsRun.workspace_id == workspace_id)
    if deployment_id:
        query = query.where(GitOpsRun.deployment_id == deployment_id)
    if state:
        query = query.where(GitOpsRun.status == state.casefold())
    return list(
        db.scalars(
            query.order_by(GitOpsRun.created_at.desc(), GitOpsRun.id).limit(limit).offset(offset)
        ).all()
    )


@router.get(
    "/workspaces/{workspace_id}/gitops-runs/{gitops_run_id}",
    response_model=GitOpsRunDetail,
    summary="Read rollout history and Argo CD reconciliation details",
)
def get_gitops_run(
    workspace_id: UUID,
    gitops_run_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> GitOpsRun:
    require_workspace_access(db, identity[0], workspace_id)
    return _load_run(db, workspace_id, gitops_run_id, detail=True)


@router.post(
    "/workspaces/{workspace_id}/gitops-runs/{gitops_run_id}/retry",
    response_model=GitOpsRunContract,
    summary="Retry a blocked GitOps run after fixing provider or target configuration",
)
def retry_gitops_run(
    workspace_id: UUID,
    gitops_run_id: UUID,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> GitOpsRun:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=GITOPS_OPERATOR_ROLES)
    run = _load_run(db, workspace_id, gitops_run_id)
    if run.status != "blocked":
        raise HTTPException(status_code=409, detail="Only blocked GitOps runs can be retried")
    if not settings.argocd_configured:
        raise HTTPException(status_code=409, detail="Argo CD integration is not configured")
    deployment = db.get(Deployment, run.deployment_id)
    if deployment is None or deployment.status not in {"ready_to_deploy", "rollback_pending"}:
        raise HTTPException(
            status_code=409, detail="Deployment is no longer eligible for GitOps sync"
        )
    target = _load_target_for_deployment(db, workspace_id, deployment)
    if not target.enabled:
        raise HTTPException(status_code=409, detail="Argo CD target is disabled")
    run.target_id = target.id
    run.application_name = target.application_name
    if run.action_kind == "deploy":
        build_run = db.get(BuildRun, run.build_run_id) if run.build_run_id else None
        if build_run is None or not build_run.commit_sha:
            raise HTTPException(
                status_code=409, detail="Build run has no immutable commit to deploy"
            )
        artifact, error_code, error_message = _artifact_for_retry(db, build_run, target)
        if error_code:
            raise HTTPException(status_code=409, detail=error_message)
        run.desired_revision = build_run.commit_sha
        run.artifact_uri = artifact.uri
        run.artifact_digest = artifact.digest
    try:
        transition_gitops_run(
            db,
            run,
            "queued",
            event_type="retry.requested",
            message="GitOps run returned to the durable reconciliation queue.",
            actor=actor,
            details={"previous_failure_code": run.failure_code},
            target_id=target.id,
            application_name=target.application_name,
            next_poll_at=None,
        )
        db.commit()
    except GitOpsTransitionConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.refresh(run)
    return run


def _load_target_for_deployment(
    db: Session, workspace_id: UUID, deployment: Deployment
) -> GitOpsTarget:
    target = db.scalar(
        select(GitOpsTarget).where(
            GitOpsTarget.workspace_id == workspace_id,
            GitOpsTarget.service_id == deployment.service_id,
            GitOpsTarget.environment_id == deployment.environment_id,
        )
    )
    if target is None:
        raise HTTPException(
            status_code=409, detail="Register an Argo CD target for this service/environment"
        )
    return target


def _artifact_for_retry(
    db: Session, build_run: BuildRun, target: GitOpsTarget
) -> tuple[BuildArtifact | None, str | None, str | None]:
    query = select(BuildArtifact).where(BuildArtifact.build_run_id == build_run.id)
    if target.artifact_name:
        query = query.where(BuildArtifact.name == target.artifact_name)
    artifacts = list(db.scalars(query.order_by(BuildArtifact.created_at)).all())
    if len(artifacts) == 1:
        return artifacts[0], None, None
    if not artifacts:
        return None, "gitops_artifact_missing", "Configured artifact was not reported by the build"
    return (
        None,
        "gitops_artifact_selection_required",
        "Configure an artifact name to select one build output",
    )


@router.post(
    "/workspaces/{workspace_id}/deployments/{deployment_id}/rollback",
    response_model=GitOpsRunContract,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue an audited rollback to the previous healthy GitOps revision",
)
def rollback_deployment(
    workspace_id: UUID,
    deployment_id: UUID,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> GitOpsRun:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=GITOPS_OPERATOR_ROLES)
    deployment = db.scalar(
        select(Deployment).where(
            Deployment.workspace_id == workspace_id,
            Deployment.id == deployment_id,
        )
    )
    if deployment is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    try:
        run = request_manual_rollback(db, deployment, actor)
        db.commit()
    except (ValueError, DeploymentTransitionConflict) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.refresh(run)
    return run
