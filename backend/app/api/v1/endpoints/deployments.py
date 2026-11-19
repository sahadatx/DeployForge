from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_identity, get_db, require_csrf
from app.core.authorization import (
    require_verified_approval_identity,
    require_workspace_access,
)
from app.core.builds import (
    create_artifact_promotion_build_run,
    create_build_run,
    evaluate_artifact_policy_for_release,
    transition_build_run,
)
from app.core.config import settings
from app.core.deployments import (
    DeploymentTransitionConflict,
    active_deployment_freeze_until,
    append_deployment_event,
    transition_deployment,
)
from app.core.gitops import create_gitops_run, transition_gitops_run
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
    DeploymentApprovalRequest,
    DeploymentContract,
    DeploymentEventContract,
    DeploymentRequest,
    DeploymentRetryResponse,
    PromotableArtifactContract,
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


@router.get(
    "/workspaces/{workspace_id}/promotion-artifacts",
    response_model=list[PromotableArtifactContract],
    summary="List successfully deployed immutable artifacts eligible for promotion",
)
def list_promotion_artifacts(
    workspace_id: UUID,
    service_id: UUID,
    environment_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[PromotableArtifactContract]:
    require_workspace_access(db, identity[0], workspace_id)
    service = db.scalar(
        select(Service).where(Service.workspace_id == workspace_id, Service.id == service_id)
    )
    environment = db.scalar(
        select(Environment).where(
            Environment.workspace_id == workspace_id,
            Environment.id == environment_id,
        )
    )
    if service is None or environment is None:
        raise HTTPException(
            status_code=422,
            detail="Service and target environment must belong to this workspace",
        )
    successful_gitops_builds = select(GitOpsRun.build_run_id).where(
        GitOpsRun.status == "succeeded",
        GitOpsRun.action_kind == "deploy",
    )
    query = (
        select(BuildArtifact, BuildRun, Deployment)
        .join(BuildRun, BuildRun.id == BuildArtifact.build_run_id)
        .join(Deployment, Deployment.id == BuildRun.deployment_id)
        .where(
            BuildArtifact.workspace_id == workspace_id,
            BuildArtifact.artifact_type == "container",
            BuildRun.status == "succeeded",
            BuildRun.id.in_(successful_gitops_builds),
            Deployment.workspace_id == workspace_id,
            Deployment.service_id == service_id,
            Deployment.environment_id != environment_id,
            Deployment.status == "succeeded",
        )
    )
    if settings.require_verified_supply_chain:
        query = query.where(BuildArtifact.evidence_status == "verified")
    rows = db.execute(
        query.order_by(BuildArtifact.created_at.desc(), BuildArtifact.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        PromotableArtifactContract(
            id=artifact.id,
            name=artifact.name,
            uri=artifact.uri,
            digest=artifact.digest,
            artifact_type=artifact.artifact_type,
            evidence_status=artifact.evidence_status,
            source_build_run_id=build_run.id,
            source_deployment_id=deployment.id,
            source_environment_id=deployment.environment_id,
            source_version=deployment.version,
            source_ref=build_run.source_ref,
            commit_sha=build_run.commit_sha,
            created_at=artifact.created_at,
        )
        for artifact, build_run, deployment in rows
    ]


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
                or existing.promoted_artifact_id != payload.artifact_id
                or existing.promotion_gitops_revision != payload.gitops_revision
                or (
                    payload.artifact_id is None
                    and existing.source_ref != (payload.source_ref or payload.version)
                )
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

    source_build_run = None
    source_artifact = None
    if payload.artifact_id is not None:
        source = db.execute(
            select(BuildArtifact, BuildRun, Deployment)
            .join(BuildRun, BuildRun.id == BuildArtifact.build_run_id)
            .join(Deployment, Deployment.id == BuildRun.deployment_id)
            .where(
                BuildArtifact.id == payload.artifact_id,
                BuildArtifact.workspace_id == workspace_id,
                BuildArtifact.artifact_type == "container",
                BuildRun.status == "succeeded",
                Deployment.workspace_id == workspace_id,
                Deployment.service_id == service.id,
                Deployment.environment_id != environment.id,
                Deployment.status == "succeeded",
            )
        ).first()
        if source is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Promotion artifact must belong to this service and have a healthy "
                    "deployment in another environment"
                ),
            )
        source_artifact, source_build_run, _source_deployment = source
        successful_source_run = db.scalar(
            select(GitOpsRun.id).where(
                GitOpsRun.build_run_id == source_build_run.id,
                GitOpsRun.action_kind == "deploy",
                GitOpsRun.status == "succeeded",
            )
        )
        if successful_source_run is None:
            raise HTTPException(
                status_code=422,
                detail="Promotion requires a successful GitOps deployment of the source artifact",
            )
        if settings.require_verified_supply_chain and source_artifact.evidence_status != "verified":
            raise HTTPException(
                status_code=422,
                detail="Production promotion requires independently verified artifact evidence",
            )
        target = db.scalar(
            select(GitOpsTarget).where(
                GitOpsTarget.workspace_id == workspace_id,
                GitOpsTarget.service_id == service.id,
                GitOpsTarget.environment_id == environment.id,
                GitOpsTarget.enabled.is_(True),
            )
        )
        if target and target.artifact_name and target.artifact_name != source_artifact.name:
            raise HTTPException(
                status_code=422,
                detail=(
                    "The selected artifact does not match the GitOps target's configured "
                    "artifact name"
                ),
            )

    deployment = Deployment(
        workspace_id=workspace_id,
        service_id=service.id,
        environment_id=environment.id,
        version=payload.version,
        source_ref=(
            source_build_run.source_ref
            if source_build_run is not None
            else payload.source_ref or payload.version
        ),
        promoted_artifact_id=source_artifact.id if source_artifact else None,
        promotion_gitops_revision=payload.gitops_revision,
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
                "promoted_artifact_id": str(deployment.promoted_artifact_id)
                if deployment.promoted_artifact_id
                else None,
                "promotion_gitops_revision": deployment.promotion_gitops_revision,
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
                and existing.promoted_artifact_id == payload.artifact_id
                and existing.promotion_gitops_revision == payload.gitops_revision
                and (
                    payload.artifact_id is not None
                    or existing.source_ref == (payload.source_ref or payload.version)
                )
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
    environment = db.get(Environment, deployment.environment_id)
    freeze_until = active_deployment_freeze_until(environment) if environment else None
    if freeze_until is not None:
        raise HTTPException(
            status_code=409,
            detail=f"This environment is in a deployment freeze until {freeze_until.isoformat()}",
        )
    if deployment.requested_by == str(actor.id):
        raise HTTPException(
            status_code=403, detail="The requester cannot approve their own deployment"
        )
    require_verified_approval_identity(
        db,
        actor,
        workspace_id=workspace_id,
        resource_type="deployment",
        resource_id=deployment.id,
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

        promotion_source: tuple[BuildRun, BuildArtifact] | None = None
        if approved and deployment.promoted_artifact_id is not None:
            artifact = db.get(BuildArtifact, deployment.promoted_artifact_id)
            source_build = db.get(BuildRun, artifact.build_run_id) if artifact is not None else None
            if (
                artifact is None
                or source_build is None
                or source_build.status != "succeeded"
                or deployment.promotion_gitops_revision is None
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The immutable source artifact or target manifest revision is unavailable"
                    ),
                )
            promotion_source = (source_build, artifact)

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
            if promotion_source is not None:
                source_build, source_artifact = promotion_source
                build_run = create_artifact_promotion_build_run(
                    db,
                    deployment,
                    source_build,
                    source_artifact,
                    gitops_revision=deployment.promotion_gitops_revision,
                    actor=actor,
                )
                transition_deployment(
                    db,
                    deployment,
                    "building",
                    event_type="promotion.artifact_reused",
                    message=(
                        "Approval granted; the previously deployed artifact is reused "
                        "without rebuilding."
                    ),
                    actor=actor,
                    details={
                        "build_run_id": str(build_run.id),
                        "source_build_run_id": str(source_build.id),
                        "source_artifact_id": str(source_artifact.id),
                        "artifact_digest": source_artifact.digest,
                        "gitops_revision": deployment.promotion_gitops_revision,
                    },
                )
                try:
                    evaluation = evaluate_artifact_policy_for_release(db, deployment, build_run)
                except ValueError as exc:
                    transition_deployment(
                        db,
                        deployment,
                        "failed",
                        event_type="policy.context_unavailable",
                        message=(
                            "Artifact policy evaluation context is unavailable for this promotion."
                        ),
                        actor_type="policy",
                        actor_id="policy-engine",
                        details={"build_run_id": str(build_run.id)},
                        failure_code="policy_context_unavailable",
                        failure_message=str(exc),
                    )
                else:
                    if evaluation.decision == "deny":
                        message = (
                            "Promoted artifact was blocked by the enforced workspace policy set."
                        )
                        transition_deployment(
                            db,
                            deployment,
                            "failed",
                            event_type="policy.blocked_after_build",
                            message=message,
                            actor_type="policy",
                            actor_id="policy-engine",
                            details={
                                "build_run_id": str(build_run.id),
                                "policy_evaluation_id": str(evaluation.id),
                                "policy_snapshot_sha256": evaluation.policy_snapshot_sha256,
                            },
                            failure_code="policy_denied",
                            failure_message=message,
                        )
                    elif evaluation.requires_approval:
                        transition_deployment(
                            db,
                            deployment,
                            "awaiting_release_approval",
                            event_type="policy.release_approval_required",
                            message=(
                                "Artifact policy requires an independent approval before "
                                "GitOps deployment."
                            ),
                            actor_type="policy",
                            actor_id="policy-engine",
                            details={
                                "build_run_id": str(build_run.id),
                                "policy_evaluation_id": str(evaluation.id),
                                "policy_snapshot_sha256": evaluation.policy_snapshot_sha256,
                            },
                        )
                    else:
                        transition_deployment(
                            db,
                            deployment,
                            "ready_to_deploy",
                            event_type="promotion.ready",
                            message=(
                                "Promoted artifact passed policy and is ready for target "
                                "manifest reconciliation."
                            ),
                            actor=actor,
                            details={
                                "build_run_id": str(build_run.id),
                                "artifact_digest": source_artifact.digest,
                                "gitops_revision": deployment.promotion_gitops_revision,
                                "policy_evaluation_id": str(evaluation.id),
                                "policy_snapshot_sha256": evaluation.policy_snapshot_sha256,
                            },
                        )
                        create_gitops_run(db, deployment, build_run, actor=actor)
            else:
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
