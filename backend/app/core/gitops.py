"""Durable Argo CD sync, health reconciliation, and revision rollback orchestration."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.core.deployments import transition_deployment
from app.integrations.argocd import ArgoCDClient, ArgoCDConfiguration, ArgoCDError
from app.integrations.github_actions import GitHubDispatchError, parse_github_repository
from app.models import (
    BuildArtifact,
    BuildRun,
    Deployment,
    GitOpsRun,
    GitOpsRunEvent,
    GitOpsTarget,
    Service,
    User,
)

GITOPS_LEASE_SECONDS = 60
GITOPS_TERMINAL_STATES = {"succeeded", "rolled_back", "failed", "canceled"}
GITOPS_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"dispatching", "blocked", "canceled"},
    "dispatching": {"syncing", "queued", "blocked", "failed", "canceled"},
    "syncing": {"verifying", "succeeded", "rolled_back", "queued", "failed", "canceled"},
    "verifying": {"syncing", "succeeded", "rolled_back", "queued", "failed", "canceled"},
    "blocked": {"queued", "canceled"},
    "failed": set(),
    "succeeded": set(),
    "rolled_back": set(),
    "canceled": set(),
}


class GitOpsTransitionConflict(Exception):
    """Raised when a concurrent worker or operator changed a GitOps run."""


def _utc_datetime(value: datetime | None) -> datetime | None:
    """SQLite returns timezone-aware SQLAlchemy values as naive UTC datetimes."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def append_gitops_event(
    db: Session,
    gitops_run: GitOpsRun,
    *,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    message: str,
    actor: User | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    details: dict | None = None,
) -> GitOpsRunEvent:
    sequence = (
        db.scalar(
            select(func.max(GitOpsRunEvent.sequence)).where(
                GitOpsRunEvent.gitops_run_id == gitops_run.id
            )
        )
        or 0
    ) + 1
    event = GitOpsRunEvent(
        gitops_run_id=gitops_run.id,
        workspace_id=gitops_run.workspace_id,
        sequence=sequence,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        actor_type=actor_type or ("user" if actor else "system"),
        actor_id=actor_id or (str(actor.id) if actor else None),
        message=message,
        details=dict(details or {}),
    )
    db.add(event)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action=f"gitops_run.{event_type}",
        resource_type="gitops_run",
        resource_id=gitops_run.id,
        workspace_id=gitops_run.workspace_id,
        details={
            "from_status": from_status,
            "to_status": to_status,
            "gitops_event_id": str(event.id),
            "application_name": gitops_run.application_name,
            "action_kind": gitops_run.action_kind,
            **(details or {}),
        },
    )
    return event


def transition_gitops_run(
    db: Session,
    gitops_run: GitOpsRun,
    to_status: str,
    *,
    event_type: str,
    message: str,
    actor: User | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    details: dict | None = None,
    failure_code: str | None = None,
    failure_message: str | None = None,
    **updates: object,
) -> GitOpsRunEvent:
    from_status = gitops_run.status
    if to_status not in GITOPS_TRANSITIONS.get(from_status, set()):
        raise ValueError(f"Invalid GitOps transition: {from_status} -> {to_status}")
    now = datetime.now(UTC)
    values: dict = {"status": to_status, "updated_at": now, **updates}
    if to_status == "dispatching" and gitops_run.started_at is None:
        values["started_at"] = now
    if to_status == "queued":
        values.update(next_poll_at=now, failure_code=None, failure_message=None)
    if to_status in GITOPS_TERMINAL_STATES:
        values["finished_at"] = now
        values["next_poll_at"] = None
    if to_status == "blocked":
        values["failure_code"] = failure_code or "gitops_configuration_required"
        values["failure_message"] = failure_message or message
    if to_status == "failed":
        values["failure_code"] = failure_code or "gitops_sync_failed"
        values["failure_message"] = failure_message or message
    if to_status != "dispatching":
        values["lease_owner"] = None
        values["lease_expires_at"] = None
    result = db.execute(
        update(GitOpsRun)
        .where(GitOpsRun.id == gitops_run.id, GitOpsRun.status == from_status)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise GitOpsTransitionConflict("GitOps run state changed concurrently")
    db.refresh(gitops_run)
    return append_gitops_event(
        db,
        gitops_run,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        message=message,
        actor=actor,
        actor_type=actor_type,
        actor_id=actor_id,
        details=details,
    )


def _target_for_deployment(db: Session, deployment: Deployment) -> GitOpsTarget | None:
    return db.scalar(
        select(GitOpsTarget).where(
            GitOpsTarget.workspace_id == deployment.workspace_id,
            GitOpsTarget.service_id == deployment.service_id,
            GitOpsTarget.environment_id == deployment.environment_id,
        )
    )


def _artifact_for_target(
    db: Session, build_run: BuildRun, target: GitOpsTarget | None
) -> tuple[BuildArtifact | None, str | None, str | None]:
    artifacts = list(
        db.scalars(
            select(BuildArtifact)
            .where(BuildArtifact.build_run_id == build_run.id)
            .order_by(BuildArtifact.created_at, BuildArtifact.id)
        ).all()
    )
    if target and target.artifact_name:
        matching = [artifact for artifact in artifacts if artifact.name == target.artifact_name]
        if len(matching) == 1:
            return matching[0], None, None
        if not matching:
            return (
                None,
                "gitops_artifact_not_found",
                "Configured artifact name was not reported by the build.",
            )
        return (
            None,
            "gitops_artifact_ambiguous",
            "Build reported more than one artifact with the configured name.",
        )
    if len(artifacts) == 1:
        return artifacts[0], None, None
    if not artifacts:
        return (
            None,
            "gitops_artifact_missing",
            "A successful build must have a stored artifact before deployment.",
        )
    return (
        None,
        "gitops_artifact_selection_required",
        "Configure the target artifact name when a build reports multiple artifacts.",
    )


def create_gitops_run(
    db: Session,
    deployment: Deployment,
    build_run: BuildRun,
    *,
    actor: User | None = None,
) -> GitOpsRun:
    target = _target_for_deployment(db, deployment)
    artifact, failure_code, failure_message = _artifact_for_target(db, build_run, target)
    if target is None:
        failure_code = "gitops_target_missing"
        failure_message = (
            "Register an Argo CD target for this service and environment before deployment."
        )
    elif not target.enabled:
        failure_code = "gitops_target_disabled"
        failure_message = "The configured Argo CD target is disabled."
    attempt_number = (
        db.scalar(
            select(func.max(GitOpsRun.attempt_number)).where(
                GitOpsRun.deployment_id == deployment.id
            )
        )
        or 0
    ) + 1
    run = GitOpsRun(
        workspace_id=deployment.workspace_id,
        deployment_id=deployment.id,
        build_run_id=build_run.id,
        target_id=target.id if target else None,
        attempt_number=attempt_number,
        action_kind="deploy",
        application_name=target.application_name if target else "unconfigured",
        desired_revision=build_run.gitops_revision or build_run.commit_sha,
        artifact_uri=artifact.uri if artifact else None,
        artifact_digest=artifact.digest if artifact else None,
        status="blocked" if failure_code else "queued",
        failure_code=failure_code,
        failure_message=failure_message,
        max_attempts=3,
        next_poll_at=datetime.now(UTC) if failure_code is None else None,
    )
    db.add(run)
    db.flush()
    append_gitops_event(
        db,
        run,
        event_type="queued" if failure_code is None else "blocked",
        from_status=None,
        to_status=run.status,
        message=(
            "Build evidence is ready; GitOps reconciliation was queued."
            if failure_code is None
            else failure_message or "GitOps reconciliation is blocked by target configuration."
        ),
        actor=actor,
        actor_type="system" if actor is None else None,
        details={
            "deployment_id": str(deployment.id),
            "build_run_id": str(build_run.id),
            "target_id": str(target.id) if target else None,
            "desired_revision": run.desired_revision,
            "source_commit_sha": build_run.commit_sha,
            "artifact_digest": run.artifact_digest,
        },
    )
    return run


def _previous_successful_run(db: Session, target_id: UUID, *, exclude_id: UUID) -> GitOpsRun | None:
    return db.scalar(
        select(GitOpsRun)
        .where(
            GitOpsRun.target_id == target_id,
            GitOpsRun.id != exclude_id,
            GitOpsRun.status == "succeeded",
            GitOpsRun.action_kind == "deploy",
        )
        .order_by(GitOpsRun.created_at.desc(), GitOpsRun.attempt_number.desc())
        .limit(1)
    )


def _create_rollback_run(
    db: Session,
    deployment: Deployment,
    failed_run: GitOpsRun,
    previous_run: GitOpsRun,
    *,
    reason: str,
) -> GitOpsRun:
    attempt_number = (
        db.scalar(
            select(func.max(GitOpsRun.attempt_number)).where(
                GitOpsRun.deployment_id == deployment.id
            )
        )
        or 0
    ) + 1
    rollback = GitOpsRun(
        workspace_id=deployment.workspace_id,
        deployment_id=deployment.id,
        build_run_id=previous_run.build_run_id,
        target_id=failed_run.target_id,
        rollback_from_run_id=previous_run.id,
        attempt_number=attempt_number,
        action_kind="rollback",
        application_name=failed_run.application_name,
        desired_revision=previous_run.desired_revision,
        artifact_uri=previous_run.artifact_uri,
        artifact_digest=previous_run.artifact_digest,
        status="queued",
        max_attempts=3,
        next_poll_at=datetime.now(UTC),
    )
    db.add(rollback)
    db.flush()
    append_gitops_event(
        db,
        rollback,
        event_type="rollback.queued",
        from_status=None,
        to_status="queued",
        message="A previously healthy GitOps revision was queued for rollback.",
        actor_type="system",
        actor_id="gitops-recovery",
        details={
            "rollback_from_run_id": str(previous_run.id),
            "failed_run_id": str(failed_run.id),
            "rollback_revision": rollback.desired_revision,
            "reason": reason,
        },
    )
    return rollback


def request_manual_rollback(db: Session, deployment: Deployment, actor: User) -> GitOpsRun:
    if deployment.status != "succeeded":
        raise ValueError("Only a healthy deployment can be rolled back")
    current_run = db.scalar(
        select(GitOpsRun)
        .where(
            GitOpsRun.deployment_id == deployment.id,
            GitOpsRun.status == "succeeded",
            GitOpsRun.action_kind == "deploy",
        )
        .order_by(GitOpsRun.created_at.desc(), GitOpsRun.id.desc())
        .limit(1)
    )
    if current_run is None or current_run.target_id is None:
        raise ValueError("A successful GitOps deployment history is required for rollback")
    target_history = list(
        db.scalars(
            select(GitOpsRun)
            .where(
                GitOpsRun.target_id == current_run.target_id,
                GitOpsRun.status == "succeeded",
                GitOpsRun.action_kind == "deploy",
            )
            .order_by(GitOpsRun.created_at.desc(), GitOpsRun.id.desc())
        ).all()
    )
    if not target_history or target_history[0].id != current_run.id:
        raise ValueError("Only the latest healthy deployment for this target can be rolled back")
    previous = target_history[1] if len(target_history) > 1 else None
    if previous is None or not previous.desired_revision:
        raise ValueError("No earlier healthy GitOps revision is available")
    rollback = _create_rollback_run(
        db, deployment, current_run, previous, reason="operator_requested"
    )
    transition_deployment(
        db,
        deployment,
        "rollback_pending",
        event_type="rollback.requested",
        message="An authorized operator requested rollback to the previous healthy revision.",
        actor=actor,
        details={
            "gitops_run_id": str(rollback.id),
            "rollback_revision": rollback.desired_revision,
        },
    )
    return rollback


def _argo_client() -> ArgoCDClient | None:
    if not settings.argocd_configured:
        return None
    assert settings.argocd_auth_token is not None
    return ArgoCDClient(
        ArgoCDConfiguration(
            api_url=settings.argocd_api_url,
            token=settings.argocd_auth_token.get_secret_value(),
            timeout_seconds=settings.argocd_api_timeout_seconds,
        )
    )


def _extract_app_status(application: dict) -> dict:
    status = application.get("status") or {}
    sync = status.get("sync") or {}
    health = status.get("health") or {}
    operation = status.get("operationState") or {}
    spec = application.get("spec") or {}
    source = spec.get("source") or {}
    destination = spec.get("destination") or {}
    return {
        "project": spec.get("project"),
        "repository_url": source.get("repoURL"),
        "multi_source": bool(spec.get("sources")),
        "sync_status": sync.get("status"),
        "health_status": health.get("status"),
        "operation_phase": operation.get("phase"),
        "revision": sync.get("revision"),
        "destination_name": destination.get("name"),
        "destination_server": destination.get("server"),
    }


def _validate_application(
    db: Session, run: GitOpsRun, target: GitOpsTarget, application: dict
) -> tuple[dict, str | None, str | None]:
    observed = _extract_app_status(application)
    service = db.get(Service, run.deployment.service_id)
    if service is None or not service.repository_url:
        return (
            observed,
            "gitops_service_repository_missing",
            "Service Catalog must define the source repository before GitOps deployment.",
        )
    if observed["multi_source"]:
        return (
            observed,
            "argocd_multi_source_unsupported",
            "This release adapter requires a single-source Argo CD Application.",
        )
    if observed["project"] != target.expected_project:
        return (
            observed,
            "argocd_project_mismatch",
            "Argo CD Application project does not match the configured target.",
        )
    try:
        expected_repository = parse_github_repository(service.repository_url)
        actual_repository = parse_github_repository(observed["repository_url"] or "")
    except GitHubDispatchError:
        return (
            observed,
            "argocd_repository_mismatch",
            "Argo CD Application source must match the registered GitHub service repository.",
        )
    if expected_repository != actual_repository:
        return (
            observed,
            "argocd_repository_mismatch",
            "Argo CD Application source does not match the registered service repository.",
        )
    if (
        target.expected_cluster_name
        and observed["destination_name"] != target.expected_cluster_name
    ):
        return (
            observed,
            "argocd_cluster_mismatch",
            "Argo CD Application destination does not match the configured cluster.",
        )
    if not run.desired_revision:
        return (
            observed,
            "gitops_revision_missing",
            "Build callback did not provide an immutable source commit.",
        )
    return observed, None, None


def _deployment_failure(
    db: Session, deployment: Deployment, *, code: str, message: str, run_id: UUID
) -> None:
    if deployment.status in {
        "ready_to_deploy",
        "deploying",
        "verifying",
        "rollback_pending",
    }:
        transition_deployment(
            db,
            deployment,
            "failed",
            event_type="gitops.failed",
            message=message,
            actor_type="worker",
            actor_id="argocd-reconciler",
            details={"gitops_run_id": str(run_id), "failure_code": code},
            failure_code=code,
            failure_message=message,
        )


def _fail_rollout(
    db: Session,
    run: GitOpsRun,
    deployment: Deployment,
    *,
    code: str,
    message: str,
    details: dict | None = None,
) -> None:
    transition_gitops_run(
        db,
        run,
        "failed",
        event_type="reconciliation.failed",
        message=message,
        actor_type="worker",
        actor_id="argocd-reconciler",
        details=details,
        failure_code=code,
        failure_message=message,
    )
    previous = (
        _previous_successful_run(db, run.target_id, exclude_id=run.id)
        if run.action_kind == "deploy" and run.target_id
        else None
    )
    if previous and previous.desired_revision and deployment.status in {"deploying", "verifying"}:
        transition_deployment(
            db,
            deployment,
            "rollback_pending",
            event_type="rollback.started",
            message="Rollout health verification failed; restoring the previous healthy revision.",
            actor_type="worker",
            actor_id="argocd-reconciler",
            details={
                "failed_gitops_run_id": str(run.id),
                "rollback_revision": previous.desired_revision,
            },
        )
        _create_rollback_run(db, deployment, run, previous, reason=code)
        return
    _deployment_failure(
        db,
        deployment,
        code=code,
        message=message,
        run_id=run.id,
    )


def _block_run(
    db: Session,
    run: GitOpsRun,
    *,
    code: str,
    message: str,
    details: dict | None = None,
) -> None:
    transition_gitops_run(
        db,
        run,
        "blocked",
        event_type="configuration.blocked",
        message=message,
        actor_type="worker",
        actor_id="argocd-adapter",
        details=details,
        failure_code=code,
        failure_message=message,
    )
    db.commit()


def _schedule_dispatch_retry(db: Session, run: GitOpsRun, error: ArgoCDError) -> None:
    if run.attempt_count < run.max_attempts:
        delay = min(300, 15 * (2 ** (run.attempt_count - 1)))
        transition_gitops_run(
            db,
            run,
            "queued",
            event_type="dispatch.retry_scheduled",
            message="Argo CD is temporarily unavailable; sync dispatch was rescheduled.",
            actor_type="worker",
            actor_id="argocd-adapter",
            details={"failure_code": error.code, "retry_after_seconds": delay},
            next_poll_at=datetime.now(UTC) + timedelta(seconds=delay),
        )
        db.commit()
        return
    deployment = db.get(Deployment, run.deployment_id)
    transition_gitops_run(
        db,
        run,
        "failed",
        event_type="dispatch.failed",
        message="Argo CD sync could not be dispatched after the retry limit.",
        actor_type="worker",
        actor_id="argocd-adapter",
        details={"failure_code": error.code},
        failure_code=error.code,
        failure_message="Argo CD sync could not be dispatched after the retry limit.",
    )
    if deployment:
        _deployment_failure(
            db,
            deployment,
            code=error.code,
            message="Argo CD sync could not be dispatched after the retry limit.",
            run_id=run.id,
        )
    db.commit()


def _recover_expired_dispatches(db: Session, now: datetime) -> None:
    expired = list(
        db.scalars(
            select(GitOpsRun).where(
                GitOpsRun.status == "dispatching",
                GitOpsRun.lease_expires_at.is_not(None),
                GitOpsRun.lease_expires_at <= now,
            )
        ).all()
    )
    for run in expired:
        retry_available = run.attempt_count < run.max_attempts
        target_status = "queued" if retry_available else "failed"
        values = {
            "status": target_status,
            "lease_owner": None,
            "lease_expires_at": None,
            "updated_at": now,
        }
        if retry_available:
            values["next_poll_at"] = now
        else:
            values.update(
                finished_at=now,
                failure_code="gitops_dispatch_lease_exhausted",
                failure_message="Argo CD dispatch worker lease expired after the retry limit.",
            )
        result = db.execute(
            update(GitOpsRun)
            .where(
                GitOpsRun.id == run.id,
                GitOpsRun.status == "dispatching",
                GitOpsRun.lease_expires_at <= now,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue
        db.refresh(run)
        append_gitops_event(
            db,
            run,
            event_type=(
                "dispatch.lease_recovered" if retry_available else "dispatch.lease_exhausted"
            ),
            from_status="dispatching",
            to_status=target_status,
            message=(
                "Expired Argo CD dispatch lease returned to the durable queue."
                if retry_available
                else "Argo CD dispatch worker lease expired after the retry limit."
            ),
            actor_type="worker",
            actor_id="lease-recovery",
            details={"attempt_count": run.attempt_count},
        )
        if not retry_available:
            deployment = db.get(Deployment, run.deployment_id)
            if deployment:
                _deployment_failure(
                    db,
                    deployment,
                    code="gitops_dispatch_lease_exhausted",
                    message="Argo CD dispatch worker lease expired after the retry limit.",
                    run_id=run.id,
                )
    if expired:
        db.commit()


def _claim_queued_run(db: Session, worker_name: str, now: datetime) -> UUID | None:
    candidate = db.scalar(
        select(GitOpsRun)
        .where(
            GitOpsRun.status == "queued",
            (GitOpsRun.next_poll_at.is_(None) | (GitOpsRun.next_poll_at <= now)),
        )
        .order_by(GitOpsRun.created_at, GitOpsRun.id)
        .limit(1)
    )
    if candidate is None:
        return None
    result = db.execute(
        update(GitOpsRun)
        .where(GitOpsRun.id == candidate.id, GitOpsRun.status == "queued")
        .values(
            status="dispatching",
            attempt_count=GitOpsRun.attempt_count + 1,
            lease_owner=worker_name,
            lease_expires_at=now + timedelta(seconds=GITOPS_LEASE_SECONDS),
            started_at=candidate.started_at or now,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.refresh(candidate)
    append_gitops_event(
        db,
        candidate,
        event_type="worker.claimed",
        from_status="queued",
        to_status="dispatching",
        message="GitOps sync claimed by a worker.",
        actor_type="worker",
        actor_id=worker_name,
        details={"attempt": candidate.attempt_count},
    )
    run_id = candidate.id
    db.commit()
    return run_id


def _claim_due_reconciliation(db: Session, worker_name: str, now: datetime) -> UUID | None:
    candidate = db.scalar(
        select(GitOpsRun)
        .where(
            GitOpsRun.status.in_({"syncing", "verifying"}),
            GitOpsRun.next_poll_at.is_not(None),
            GitOpsRun.next_poll_at <= now,
            (GitOpsRun.lease_expires_at.is_(None) | (GitOpsRun.lease_expires_at <= now)),
        )
        .order_by(GitOpsRun.next_poll_at, GitOpsRun.created_at)
        .limit(1)
    )
    if candidate is None:
        return None
    result = db.execute(
        update(GitOpsRun)
        .where(
            GitOpsRun.id == candidate.id,
            GitOpsRun.status.in_({"syncing", "verifying"}),
            GitOpsRun.next_poll_at <= now,
            (GitOpsRun.lease_expires_at.is_(None) | (GitOpsRun.lease_expires_at <= now)),
        )
        .values(
            lease_owner=worker_name,
            lease_expires_at=now + timedelta(seconds=GITOPS_LEASE_SECONDS),
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    run_id = candidate.id
    db.commit()
    return run_id


def _dispatch_claimed_run(db: Session, run_id: UUID, worker_name: str) -> bool:
    run = db.get(GitOpsRun, run_id)
    if run is None or run.status != "dispatching" or run.lease_owner != worker_name:
        return False
    if not settings.argocd_configured:
        _block_run(
            db,
            run,
            code="argocd_not_configured",
            message="Configure the Argo CD HTTPS API URL and token before running GitOps syncs.",
            details={"required_settings": ["ARGOCD_API_URL", "ARGOCD_AUTH_TOKEN"]},
        )
        return True
    target = db.get(GitOpsTarget, run.target_id) if run.target_id else None
    deployment = db.get(Deployment, run.deployment_id)
    if target is None or not target.enabled:
        _block_run(
            db,
            run,
            code="gitops_target_unavailable",
            message="The configured Argo CD target is missing or disabled.",
        )
        return True
    if deployment is None or deployment.status not in {"ready_to_deploy", "rollback_pending"}:
        transition_gitops_run(
            db,
            run,
            "canceled",
            event_type="sync.canceled",
            message="GitOps sync canceled because its deployment is no longer eligible.",
            actor_type="worker",
            actor_id=worker_name,
        )
        db.commit()
        return True
    if not run.desired_revision:
        _block_run(
            db,
            run,
            code="gitops_revision_missing",
            message="An immutable source commit is required for Argo CD sync.",
        )
        return True
    db.commit()
    client = _argo_client()
    assert client is not None
    try:
        application = client.get_application(run.application_name)
    except ArgoCDError as exc:
        if exc.retryable:
            db.refresh(run)
            _schedule_dispatch_retry(db, run, exc)
        else:
            _block_run(db, run, code=exc.code, message=str(exc))
        return True
    observed, failure_code, failure_message = _validate_application(db, run, target, application)
    run.destination_name = observed["destination_name"]
    run.destination_server = observed["destination_server"]
    if failure_code:
        _block_run(
            db,
            run,
            code=failure_code,
            message=failure_message or "Argo CD application did not match its registered target.",
            details={
                "project": observed["project"],
                "destination_name": observed["destination_name"],
                "destination_server": observed["destination_server"],
            },
        )
        return True
    try:
        client.sync_application(run.application_name, run.desired_revision)
    except ArgoCDError as exc:
        db.refresh(run)
        if exc.retryable:
            _schedule_dispatch_retry(db, run, exc)
        else:
            _block_run(db, run, code=exc.code, message=str(exc))
        return True
    now = datetime.now(UTC)
    transition_gitops_run(
        db,
        run,
        "syncing",
        event_type="sync.dispatched",
        message="Argo CD accepted a sync for the immutable GitOps manifest revision.",
        actor_type="worker",
        actor_id=worker_name,
        details={"desired_revision": run.desired_revision},
        next_poll_at=now + timedelta(seconds=settings.gitops_reconcile_interval_seconds),
        callback_deadline_at=now + timedelta(seconds=settings.gitops_sync_timeout_seconds),
        destination_name=observed["destination_name"],
        destination_server=observed["destination_server"],
        sync_status=observed["sync_status"],
        health_status=observed["health_status"],
        operation_phase=observed["operation_phase"],
        observed_revision=observed["revision"],
    )
    if run.action_kind == "deploy" and deployment.status == "ready_to_deploy":
        transition_deployment(
            db,
            deployment,
            "deploying",
            event_type="gitops.sync_dispatched",
            message="Argo CD accepted the desired revision for the target cluster.",
            actor_type="worker",
            actor_id=worker_name,
            details={"gitops_run_id": str(run.id), "application_name": run.application_name},
        )
    db.commit()
    return True


def _finish_healthy_run(
    db: Session, run: GitOpsRun, deployment: Deployment, observed: dict
) -> None:
    is_rollback = run.action_kind == "rollback"
    transition_gitops_run(
        db,
        run,
        "rolled_back" if is_rollback else "succeeded",
        event_type="rollback.healthy" if is_rollback else "sync.healthy",
        message=(
            "Argo CD restored the previous revision and reported Synced/Healthy."
            if is_rollback
            else "Argo CD reports the requested immutable GitOps revision Synced and Healthy."
        ),
        actor_type="worker",
        actor_id="argocd-reconciler",
        details={
            "desired_revision": run.desired_revision,
            "observed_revision": observed["revision"],
            "sync_status": observed["sync_status"],
            "health_status": observed["health_status"],
        },
        sync_status=observed["sync_status"],
        health_status=observed["health_status"],
        operation_phase=observed["operation_phase"],
        observed_revision=observed["revision"],
        destination_name=observed["destination_name"],
        destination_server=observed["destination_server"],
    )
    if is_rollback:
        if deployment.status == "rollback_pending":
            transition_deployment(
                db,
                deployment,
                "rolled_back",
                event_type="rollback.verified",
                message="Rollback revision is Synced and Healthy in the target cluster.",
                actor_type="worker",
                actor_id="argocd-reconciler",
                details={"gitops_run_id": str(run.id), "revision": observed["revision"]},
            )
        return
    if deployment.status == "deploying":
        transition_deployment(
            db,
            deployment,
            "verifying",
            event_type="rollout.verifying",
            message="Argo CD reports a healthy sync; final rollout state is being recorded.",
            actor_type="worker",
            actor_id="argocd-reconciler",
            details={"gitops_run_id": str(run.id)},
        )
    if deployment.status == "verifying":
        transition_deployment(
            db,
            deployment,
            "succeeded",
            event_type="rollout.succeeded",
            message="Deployment revision is Synced and Healthy in the target cluster.",
            actor_type="worker",
            actor_id="argocd-reconciler",
            details={
                "gitops_run_id": str(run.id),
                "revision": observed["revision"],
                "artifact_digest": run.artifact_digest,
            },
        )


def _poll_claimed_run(db: Session, run_id: UUID, worker_name: str) -> bool:
    run = db.get(GitOpsRun, run_id)
    if run is None or run.status not in {"syncing", "verifying"} or run.lease_owner != worker_name:
        return False
    deployment = db.get(Deployment, run.deployment_id)
    target = db.get(GitOpsTarget, run.target_id) if run.target_id else None
    if deployment is None or target is None:
        transition_gitops_run(
            db,
            run,
            "failed",
            event_type="reconciliation.target_missing",
            message="GitOps target or deployment was removed during reconciliation.",
            actor_type="worker",
            actor_id=worker_name,
            failure_code="gitops_target_removed",
            failure_message="GitOps target or deployment was removed during reconciliation.",
        )
        if deployment:
            _deployment_failure(
                db,
                deployment,
                code="gitops_target_removed",
                message="GitOps target or deployment was removed during reconciliation.",
                run_id=run.id,
            )
        db.commit()
        return True
    client = _argo_client()
    if client is None:
        _fail_rollout(
            db,
            run,
            deployment,
            code="argocd_not_configured",
            message="Argo CD provider became unavailable while the rollout was active.",
        )
        db.commit()
        return True
    db.commit()
    try:
        application = client.get_application(run.application_name)
    except ArgoCDError as exc:
        if exc.retryable:
            deadline = _utc_datetime(run.callback_deadline_at)
            if deadline is not None and deadline <= datetime.now(UTC):
                _fail_rollout(
                    db,
                    run,
                    deployment,
                    code="argocd_sync_timeout",
                    message="Argo CD status could not be read before the rollout deadline.",
                )
                db.commit()
                return True
            run.lease_owner = None
            run.lease_expires_at = None
            run.next_poll_at = datetime.now(UTC) + timedelta(
                seconds=settings.gitops_reconcile_interval_seconds
            )
            run.updated_at = datetime.now(UTC)
            append_gitops_event(
                db,
                run,
                event_type="reconciliation.retry",
                from_status=run.status,
                to_status=run.status,
                message="Argo CD status could not be read; reconciliation will retry.",
                actor_type="worker",
                actor_id=worker_name,
                details={"failure_code": exc.code},
            )
            db.commit()
            return True
        _fail_rollout(
            db,
            run,
            deployment,
            code=exc.code,
            message="Argo CD application could not be read during rollout verification.",
        )
        db.commit()
        return True
    observed, failure_code, failure_message = _validate_application(db, run, target, application)
    if failure_code:
        _fail_rollout(
            db,
            run,
            deployment,
            code=failure_code,
            message=failure_message
            or "Argo CD application no longer matches the registered target.",
        )
        db.commit()
        return True

    now = datetime.now(UTC)
    failed_phase = (observed["operation_phase"] or "").casefold() in {"failed", "error"}
    degraded_health = (observed["health_status"] or "").casefold() in {"degraded", "missing"}
    deadline = _utc_datetime(run.callback_deadline_at)
    timed_out = deadline is not None and deadline <= now
    if failed_phase or degraded_health or timed_out:
        code = (
            "argocd_sync_timeout"
            if timed_out
            else "argocd_health_degraded"
            if degraded_health
            else "argocd_sync_failed"
        )
        message = (
            "Argo CD did not report a healthy rollout before the configured deadline."
            if timed_out
            else "Argo CD reported a failed operation or degraded application health."
        )
        _fail_rollout(
            db,
            run,
            deployment,
            code=code,
            message=message,
            details={
                "operation_phase": observed["operation_phase"],
                "health_status": observed["health_status"],
                "sync_status": observed["sync_status"],
            },
        )
        db.commit()
        return True

    is_healthy = (
        observed["sync_status"] == "Synced"
        and observed["health_status"] == "Healthy"
        and (observed["revision"] or "").casefold() == (run.desired_revision or "").casefold()
    )
    if is_healthy:
        _finish_healthy_run(db, run, deployment, observed)
        db.commit()
        return True

    next_status = (
        "verifying" if (observed["operation_phase"] or "").casefold() == "succeeded" else "syncing"
    )
    if run.status != next_status:
        transition_gitops_run(
            db,
            run,
            next_status,
            event_type="sync.operation_succeeded"
            if next_status == "verifying"
            else "reconciliation.progressing",
            message=(
                "Argo CD sync operation completed; waiting for application health verification."
                if next_status == "verifying"
                else "Argo CD is still reconciling the desired revision."
            ),
            actor_type="worker",
            actor_id=worker_name,
            details={
                "operation_phase": observed["operation_phase"],
                "health_status": observed["health_status"],
                "sync_status": observed["sync_status"],
            },
            next_poll_at=now + timedelta(seconds=settings.gitops_reconcile_interval_seconds),
            sync_status=observed["sync_status"],
            health_status=observed["health_status"],
            operation_phase=observed["operation_phase"],
            observed_revision=observed["revision"],
            destination_name=observed["destination_name"],
            destination_server=observed["destination_server"],
        )
    else:
        run.sync_status = observed["sync_status"]
        run.health_status = observed["health_status"]
        run.operation_phase = observed["operation_phase"]
        run.observed_revision = observed["revision"]
        run.destination_name = observed["destination_name"]
        run.destination_server = observed["destination_server"]
        run.next_poll_at = now + timedelta(seconds=settings.gitops_reconcile_interval_seconds)
        run.lease_owner = None
        run.lease_expires_at = None
        run.updated_at = now
        db.add(run)
    db.commit()
    return True


def process_next_gitops_run(db: Session, worker_name: str) -> bool:
    now = datetime.now(UTC)
    _recover_expired_dispatches(db, now)
    run_id = _claim_queued_run(db, worker_name, now)
    if run_id:
        return _dispatch_claimed_run(db, run_id, worker_name)
    run_id = _claim_due_reconciliation(db, worker_name, now)
    return _poll_claimed_run(db, run_id, worker_name) if run_id else False


def read_target_status(target: GitOpsTarget) -> dict:
    client = _argo_client()
    if client is None:
        return {
            "application_name": target.application_name,
            "provider_status": "disconnected",
            "project": None,
            "sync_status": None,
            "health_status": None,
            "operation_phase": None,
            "revision": None,
            "destination_name": None,
            "destination_server": None,
        }
    try:
        application = client.get_application(target.application_name)
    except ArgoCDError as exc:
        return {
            "application_name": target.application_name,
            "provider_status": "unavailable",
            "failure_code": exc.code,
            "project": None,
            "sync_status": None,
            "health_status": None,
            "operation_phase": None,
            "revision": None,
            "destination_name": None,
            "destination_server": None,
        }
    return {
        "application_name": target.application_name,
        "provider_status": "connected",
        **_extract_app_status(application),
    }
