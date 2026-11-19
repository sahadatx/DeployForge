"""Durable Argo CD and Flux sync, health reconciliation, and rollback orchestration."""

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.core.deployments import active_deployment_freeze_until, transition_deployment
from app.integrations.argo_rollouts import (
    ArgoRolloutsClient,
    ArgoRolloutsConfiguration,
    ArgoRolloutsError,
    normalize_rollout_info,
)
from app.integrations.argocd import ArgoCDClient, ArgoCDConfiguration, ArgoCDError
from app.integrations.flux import (
    FluxClient,
    FluxConfiguration,
    FluxError,
    canonical_git_repository_url,
)
from app.integrations.github_actions import GitHubDispatchError, parse_github_repository
from app.models import (
    BuildArtifact,
    BuildRun,
    Deployment,
    Environment,
    GitOpsRolloutAction,
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
        values.setdefault("next_poll_at", now)
        values.update(failure_code=None, failure_message=None)
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
    environment = db.get(Environment, deployment.environment_id)
    freeze_until = active_deployment_freeze_until(environment) if environment else None
    artifact, failure_code, failure_message = _artifact_for_target(db, build_run, target)
    if target is None:
        failure_code = "gitops_target_missing"
        failure_message = (
            "Register a GitOps target for this service and environment before deployment."
        )
    elif not target.enabled:
        failure_code = "gitops_target_disabled"
        failure_message = "The configured GitOps target is disabled."
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
        next_poll_at=(freeze_until or datetime.now(UTC)) if failure_code is None else None,
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
            (
                "Build evidence is ready; GitOps reconciliation is queued after the active "
                "deployment freeze."
                if freeze_until is not None
                else "Build evidence is ready; GitOps reconciliation was queued."
            )
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
            "deployment_freeze_until": freeze_until.isoformat() if freeze_until else None,
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


def _flux_client() -> FluxClient | None:
    if not settings.flux_configured:
        return None
    assert settings.flux_auth_token is not None
    return FluxClient(
        FluxConfiguration(
            api_url=settings.flux_api_url,
            token=settings.flux_auth_token.get_secret_value(),
            timeout_seconds=settings.flux_api_timeout_seconds,
        )
    )


def _gitops_provider_configured(target: GitOpsTarget) -> bool:
    return settings.flux_configured if target.provider == "flux" else settings.argocd_configured


def _gitops_provider_name(provider: str) -> str:
    return "Flux" if provider == "flux" else "Argo CD"


def _flux_observation(
    db: Session,
    run: GitOpsRun,
    target: GitOpsTarget,
    environment: Environment | None,
    client: FluxClient,
    *,
    require_pinned_commit: bool = True,
) -> tuple[dict, str | None, str | None]:
    namespace = target.reconciliation_namespace
    if not namespace:
        return {}, "flux_namespace_missing", "Flux target must define a Kustomization namespace."
    try:
        kustomization = client.get_kustomization(namespace, run.application_name)
    except FluxError:
        raise
    metadata = kustomization.get("metadata")
    spec = kustomization.get("spec")
    status = kustomization.get("status")
    if not isinstance(metadata, dict) or not isinstance(spec, dict) or not isinstance(status, dict):
        return {}, "flux_invalid_resource", "Flux returned a malformed Kustomization resource."
    if metadata.get("name") != run.application_name or metadata.get("namespace") != namespace:
        return {}, "flux_target_mismatch", "Flux returned a different Kustomization resource."
    if environment is None or not environment.namespace or not environment.cluster_name:
        return (
            {},
            "flux_environment_unbound",
            "Environment must define a cluster and target namespace.",
        )
    if (
        target.expected_cluster_name != settings.flux_cluster_name
        or environment.cluster_name != settings.flux_cluster_name
    ):
        return (
            {},
            "flux_cluster_mismatch",
            "Flux API cluster does not match the target environment.",
        )
    target_namespace = spec.get("targetNamespace") or namespace
    if target_namespace != environment.namespace:
        return (
            {},
            "flux_namespace_mismatch",
            "Flux Kustomization target namespace does not match the environment.",
        )
    if spec.get("wait") is not True and not spec.get("healthChecks"):
        return (
            {},
            "flux_health_checks_required",
            "Flux Kustomization must set wait=true or define healthChecks before deployment.",
        )
    source_ref = spec.get("sourceRef") or {}
    if not isinstance(source_ref, dict):
        return {}, "flux_source_ref_invalid", "Flux Kustomization sourceRef is malformed."
    if str(source_ref.get("kind", "GitRepository")).casefold() != "gitrepository":
        return {}, "flux_source_kind_unsupported", "Flux target must use a GitRepository source."
    source_namespace = source_ref.get("namespace") or namespace
    source_name = source_ref.get("name")
    if source_namespace != namespace or not isinstance(source_name, str) or not source_name:
        return (
            {},
            "flux_source_ref_invalid",
            "Flux sourceRef must identify a GitRepository in the target namespace.",
        )
    try:
        source = client.get_git_repository(namespace, source_name)
    except FluxError:
        raise
    source_metadata = source.get("metadata")
    source_spec = source.get("spec")
    if (
        not isinstance(source_metadata, dict)
        or source_metadata.get("name") != source_name
        or source_metadata.get("namespace") != namespace
        or not isinstance(source_spec, dict)
    ):
        return {}, "flux_source_mismatch", "Flux returned a different GitRepository source."
    service = db.get(Service, run.deployment.service_id)
    expected_repository = (
        canonical_git_repository_url(service.repository_url or "") if service else None
    )
    observed_repository = canonical_git_repository_url(str(source_spec.get("url", "")))
    if expected_repository is None or observed_repository != expected_repository:
        return (
            {},
            "flux_repository_mismatch",
            "Flux GitRepository URL does not match the registered service repository.",
        )
    source_ref = source_spec.get("ref") or {}
    if not isinstance(source_ref, dict):
        return {}, "flux_source_ref_invalid", "Flux GitRepository ref is malformed."
    pinned_commit = str(source_ref.get("commit") or "").casefold()
    desired_revision = (run.desired_revision or "").casefold()
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", desired_revision):
        return {}, "flux_revision_invalid", "Flux requires a full immutable Git commit revision."
    if require_pinned_commit and (
        not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", pinned_commit)
        or pinned_commit != desired_revision
    ):
        return (
            {},
            "flux_commit_not_pinned",
            "Flux GitRepository must pin spec.ref.commit to the exact requested source commit.",
        )
    conditions = status.get("conditions") or []
    if not isinstance(conditions, list):
        conditions = []
    condition_by_type = {
        item.get("type"): item for item in conditions if isinstance(item, dict)
    }
    ready = condition_by_type.get("Ready") or {}
    reconciling = condition_by_type.get("Reconciling") or {}
    stalled = condition_by_type.get("Stalled") or {}
    ready_true = ready.get("status") == "True"
    stalled_true = stalled.get("status") == "True"
    reconciling_true = reconciling.get("status") == "True"
    raw_revision = str(
        status.get("lastAppliedRevision") or status.get("lastAttemptedRevision") or ""
    )
    revision_match = re.search(r"(?:^|@)(?:sha1|sha256):([0-9a-fA-F]{40,64})$", raw_revision)
    revision = revision_match.group(1).lower() if revision_match else raw_revision.lower()
    exact_revision = bool(run.desired_revision) and revision == run.desired_revision.casefold()
    if stalled_true:
        sync_status, health_status, operation_phase = "OutOfSync", "Degraded", "Stalled"
    elif ready.get("status") == "False" and not reconciling_true:
        sync_status, health_status = "OutOfSync", "Degraded"
        operation_phase = ready.get("reason") or "Failed"
    elif ready_true:
        sync_status, health_status, operation_phase = (
            ("Synced", "Healthy", "Ready")
            if exact_revision
            else ("OutOfSync", "Healthy", "Ready")
        )
    elif reconciling_true:
        sync_status, health_status, operation_phase = "Progressing", "Progressing", "Reconciling"
    else:
        sync_status, health_status = "OutOfSync", "Progressing"
        operation_phase = ready.get("reason") or ready.get("status") or "Pending"
    observed = {
        "project": None,
        "repository_url": source_spec.get("url"),
        "multi_source": False,
        "sync_status": sync_status,
        "health_status": health_status,
        "operation_phase": operation_phase,
        "revision": revision or None,
        "images": [],
        "destination_name": settings.flux_cluster_name,
        "destination_server": settings.flux_api_url,
        "namespace": namespace,
        "target_namespace": target_namespace,
        "source_name": source_name,
        "ready": ready_true,
        "stalled": stalled_true,
        "reconciling": reconciling_true,
    }
    return observed, None, None


def _argo_rollouts_client() -> ArgoRolloutsClient | None:
    if not settings.argo_rollouts_credentials_configured:
        return None
    assert settings.argo_rollouts_auth_token is not None
    return ArgoRolloutsClient(
        ArgoRolloutsConfiguration(
            api_url=settings.argo_rollouts_api_url,
            token=settings.argo_rollouts_auth_token.get_secret_value(),
            timeout_seconds=settings.argo_rollouts_api_timeout_seconds,
        )
    )


def _observe_rollout(db: Session, run: GitOpsRun, target: GitOpsTarget, worker_name: str) -> None:
    client = _argo_rollouts_client()
    if client is None:
        return
    deployment = db.get(Deployment, run.deployment_id)
    environment = db.get(Environment, deployment.environment_id) if deployment else None
    if environment is None or not environment.namespace:
        return
    try:
        observed = normalize_rollout_info(
            client.get_rollout_info(environment.namespace, run.application_name)
        )
    except ArgoRolloutsError as exc:
        previous = run.rollout_snapshot or {}
        if previous.get("observation_error") == exc.code:
            return
        snapshot = dict(previous)
        snapshot["observation_error"] = exc.code
        run.rollout_snapshot = snapshot
        run.rollout_updated_at = datetime.now(UTC)
        db.add(run)
        append_gitops_event(
            db,
            run,
            event_type="rollout.observation_unavailable",
            from_status=run.status,
            to_status=run.status,
            message="Argo Rollouts stage and analysis details could not be refreshed.",
            actor_type="worker",
            actor_id=worker_name,
            details={"failure_code": exc.code},
        )
        return
    if observed == run.rollout_snapshot:
        return
    run.rollout_snapshot = observed
    run.rollout_updated_at = datetime.now(UTC)
    db.add(run)
    append_gitops_event(
        db,
        run,
        event_type="rollout.progress",
        from_status=run.status,
        to_status=run.status,
        message="Argo Rollouts stage and analysis evidence updated.",
        actor_type="worker",
        actor_id=worker_name,
        details={
            "phase": observed.get("phase"),
            "paused": observed.get("paused"),
            "current_step_index": observed.get("current_step_index"),
            "analysis_run_count": len(observed.get("analysis_runs", [])),
        },
    )


def _extract_app_status(application: dict) -> dict:
    status = application.get("status") or {}
    summary = status.get("summary") or {}
    sync = status.get("sync") or {}
    health = status.get("health") or {}
    operation = status.get("operationState") or {}
    images = summary.get("images")
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
        "images": [item for item in images[:500] if isinstance(item, str)]
        if isinstance(images, list)
        else [],
        "destination_name": destination.get("name"),
        "destination_server": destination.get("server"),
        "namespace": destination.get("namespace"),
    }


def _oci_artifact_digest_is_observed(
    artifact_uri: str | None, artifact_digest: str | None, observed_images: list[str]
) -> bool:
    """Require the exact artifact repository and digest in controller or pod image evidence."""
    if not artifact_uri or not artifact_digest:
        return False
    parsed = urlsplit(artifact_uri)
    if parsed.scheme != "oci" or not parsed.netloc or parsed.query or parsed.fragment:
        return False
    expected_repository = parsed.netloc + parsed.path
    if "@" in expected_repository:
        expected_repository = expected_repository.split("@", 1)[0]
    expected_repository = _strip_image_tag(expected_repository).casefold()
    if not expected_repository:
        return False

    for image in observed_images:
        reference, separator, observed_digest = image.strip().partition("@")
        if not separator or not observed_digest:
            continue
        if (
            _strip_image_tag(reference).casefold() == expected_repository
            and observed_digest.casefold() == artifact_digest.casefold()
        ):
            return True
    return False


def _strip_image_tag(reference: str) -> str:
    last_slash = reference.rfind("/")
    last_colon = reference.rfind(":")
    return reference[:last_colon] if last_colon > last_slash else reference


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
            actor_id="gitops-reconciler",
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
        actor_id="gitops-reconciler",
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
            actor_id="gitops-reconciler",
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


def _reschedule_poll_after_error(
    db: Session,
    run: GitOpsRun,
    deployment: Deployment,
    provider: str,
    worker_name: str,
    error: ArgoCDError | FluxError,
) -> None:
    provider_name = _gitops_provider_name(provider)
    deadline = _utc_datetime(run.callback_deadline_at)
    if error.retryable and (deadline is None or deadline > datetime.now(UTC)):
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
            message=f"{provider_name} status could not be read; reconciliation will retry.",
            actor_type="worker",
            actor_id=worker_name,
            details={"failure_code": error.code},
        )
        return
    code = f"{provider}_sync_timeout" if error.retryable else error.code
    _fail_rollout(
        db,
        run,
        deployment,
        code=code,
        message=f"{provider_name} status could not be read before the rollout deadline."
        if error.retryable
        else f"{provider_name} status could not be read during rollout verification.",
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
        actor_id="gitops-adapter",
        details=details,
        failure_code=code,
        failure_message=message,
    )
    db.commit()


def _schedule_dispatch_retry(
    db: Session, run: GitOpsRun, error: ArgoCDError | FluxError
) -> None:
    target = db.get(GitOpsTarget, run.target_id) if run.target_id else None
    provider_name = _gitops_provider_name(target.provider if target else "argocd")
    provider_actor = "flux-adapter" if provider_name == "Flux" else "argocd-adapter"
    if run.attempt_count < run.max_attempts:
        delay = min(300, 15 * (2 ** (run.attempt_count - 1)))
        transition_gitops_run(
            db,
            run,
            "queued",
            event_type="dispatch.retry_scheduled",
            message=f"{provider_name} is temporarily unavailable; sync dispatch was rescheduled.",
            actor_type="worker",
            actor_id=provider_actor,
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
        message=f"{provider_name} sync could not be dispatched after the retry limit.",
        actor_type="worker",
        actor_id=provider_actor,
        details={"failure_code": error.code},
        failure_code=error.code,
        failure_message=f"{provider_name} sync could not be dispatched after the retry limit.",
    )
    if deployment:
        _deployment_failure(
            db,
            deployment,
            code=error.code,
            message=f"{provider_name} sync could not be dispatched after the retry limit.",
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
        .join(Deployment, Deployment.id == GitOpsRun.deployment_id)
        .join(Environment, Environment.id == Deployment.environment_id)
        .where(
            GitOpsRun.status == "queued",
            (GitOpsRun.next_poll_at.is_(None) | (GitOpsRun.next_poll_at <= now)),
            or_(
                Environment.deployment_freeze_start_at.is_(None),
                Environment.deployment_freeze_start_at > now,
                Environment.deployment_freeze_until <= now,
            ),
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
    deployment = db.get(Deployment, run.deployment_id)
    environment = db.get(Environment, deployment.environment_id) if deployment else None
    freeze_until = active_deployment_freeze_until(environment) if environment else None
    if freeze_until is not None:
        transition_gitops_run(
            db,
            run,
            "queued",
            event_type="freeze.deferred",
            message="GitOps sync is deferred until the environment freeze ends.",
            actor_type="worker",
            actor_id=worker_name,
            details={"freeze_until": freeze_until.isoformat()},
            next_poll_at=freeze_until,
        )
        db.commit()
        return True
    target = db.get(GitOpsTarget, run.target_id) if run.target_id else None
    deployment = db.get(Deployment, run.deployment_id)
    if target is None or not target.enabled:
        _block_run(
            db,
            run,
            code="gitops_target_unavailable",
            message="The configured GitOps target is missing or disabled.",
        )
        return True
    provider_name = _gitops_provider_name(target.provider)
    if not _gitops_provider_configured(target):
        required_settings = (
            ["FLUX_API_URL", "FLUX_CLUSTER_NAME", "FLUX_AUTH_TOKEN"]
            if target.provider == "flux"
            else ["ARGOCD_API_URL", "ARGOCD_AUTH_TOKEN"]
        )
        _block_run(
            db,
            run,
            code=f"{target.provider}_not_configured",
            message=f"Configure {provider_name} before running GitOps reconciliation.",
            details={"required_settings": required_settings},
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
            message="An immutable source commit is required for GitOps reconciliation.",
        )
        return True
    db.commit()
    if target.provider == "flux":
        client = _flux_client()
        assert client is not None
        try:
            observed, failure_code, failure_message = _flux_observation(
                db, run, target, environment, client, require_pinned_commit=False
            )
        except FluxError as exc:
            if exc.retryable:
                db.refresh(run)
                _schedule_dispatch_retry(db, run, exc)
            else:
                _block_run(db, run, code=exc.code, message=str(exc))
            return True
        if failure_code:
            _block_run(
                db,
                run,
                code=failure_code,
                message=failure_message
                or "Flux Kustomization does not match its registered target.",
                details={
                    "destination_name": observed.get("destination_name"),
                    "namespace": observed.get("namespace"),
                },
            )
            return True
        try:
            sources = client.list_kustomizations(target.reconciliation_namespace)
            items = sources.get("items")
            if not isinstance(items, list):
                _block_run(
                    db,
                    run,
                    code="flux_kustomization_list_invalid",
                    message="Kubernetes returned an invalid Kustomization list.",
                )
                return True
            target_seen = False
            for item in items:
                if not isinstance(item, dict):
                    _block_run(
                        db,
                        run,
                        code="flux_kustomization_list_invalid",
                        message="Kubernetes returned a malformed Kustomization list item.",
                    )
                    return True
                item_metadata = item.get("metadata") or {}
                item_spec = item.get("spec") or {}
                if not isinstance(item_metadata, dict) or not isinstance(item_spec, dict):
                    _block_run(
                        db,
                        run,
                        code="flux_kustomization_list_invalid",
                        message="Kubernetes returned a malformed Kustomization list item.",
                    )
                    return True
                item_source = item_spec.get("sourceRef") or {}
                if not isinstance(item_source, dict):
                    _block_run(
                        db,
                        run,
                        code="flux_kustomization_list_invalid",
                        message="Kubernetes returned a malformed Kustomization list item.",
                    )
                    return True
                item_name = item_metadata.get("name")
                item_namespace = item_metadata.get("namespace") or target.reconciliation_namespace
                if (
                    item_name == run.application_name
                    and item_namespace == target.reconciliation_namespace
                ):
                    target_seen = True
                if (
                    str(item_source.get("kind") or "GitRepository").casefold()
                    == "gitrepository"
                    and (item_source.get("namespace") or item_namespace)
                    == target.reconciliation_namespace
                    and item_source.get("name") == observed["source_name"]
                    and item_name != run.application_name
                ):
                    _block_run(
                        db,
                        run,
                        code="flux_source_shared",
                        message=(
                            "Flux GitRepository is referenced by another Kustomization; "
                            "use a dedicated source per DeployForge target."
                        ),
                    )
                    return True
            if not target_seen:
                _block_run(
                    db,
                    run,
                    code="flux_target_not_listed",
                    message="Flux Kustomization is missing from its namespace resource list.",
                )
                return True
            client.set_git_repository_commit(
                target.reconciliation_namespace,
                observed["source_name"],
                run.desired_revision,
            )
            client.request_reconciliation(target.reconciliation_namespace, run.application_name)
        except FluxError as exc:
            db.refresh(run)
            if exc.retryable:
                _schedule_dispatch_retry(db, run, exc)
            else:
                _block_run(db, run, code=exc.code, message=str(exc))
            return True
    else:
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
        observed, failure_code, failure_message = _validate_application(
            db, run, target, application
        )
        if failure_code:
            _block_run(
                db,
                run,
                code=failure_code,
                message=failure_message
                or "Argo CD application did not match its registered target.",
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
    run.destination_name = observed.get("destination_name")
    run.destination_server = observed.get("destination_server")
    transition_gitops_run(
        db,
        run,
        "syncing",
        event_type="sync.dispatched",
        message=f"{provider_name} accepted a sync for the immutable GitOps manifest revision.",
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
            message=f"{provider_name} accepted the desired revision for the target cluster.",
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
    target = db.get(GitOpsTarget, run.target_id) if run.target_id else None
    provider_name = _gitops_provider_name(target.provider if target else "argocd")
    transition_gitops_run(
        db,
        run,
        "rolled_back" if is_rollback else "succeeded",
        event_type="rollback.healthy" if is_rollback else "sync.healthy",
        message=(
            f"{provider_name} restored the previous revision and reported Synced/Healthy."
            if is_rollback
            else (
                f"{provider_name} reports the requested immutable GitOps revision "
                "Synced and Healthy."
            )
        ),
        actor_type="worker",
        actor_id=f"{target.provider if target else 'argocd'}-reconciler",
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
                actor_id=f"{target.provider if target else 'argocd'}-reconciler",
                details={"gitops_run_id": str(run.id), "revision": observed["revision"]},
            )
        return
    if deployment.status == "deploying":
        transition_deployment(
            db,
            deployment,
            "verifying",
            event_type="rollout.verifying",
            message=(
                f"{provider_name} reports a healthy sync; final rollout state is being recorded."
            ),
            actor_type="worker",
            actor_id=f"{target.provider if target else 'argocd'}-reconciler",
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
            actor_id=f"{target.provider if target else 'argocd'}-reconciler",
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
    environment = db.get(Environment, deployment.environment_id) if deployment else None
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
    provider_name = _gitops_provider_name(target.provider)
    if not _gitops_provider_configured(target):
        _fail_rollout(
            db,
            run,
            deployment,
            code=f"{target.provider}_not_configured",
            message=f"{provider_name} provider became unavailable while the rollout was active.",
        )
        db.commit()
        return True
    db.commit()
    if target.provider == "flux":
        client = _flux_client()
        assert client is not None
        try:
            observed, failure_code, failure_message = _flux_observation(
                db, run, target, environment, client
            )
        except FluxError as exc:
            _reschedule_poll_after_error(db, run, deployment, target.provider, worker_name, exc)
            db.commit()
            return True
    else:
        client = _argo_client()
        assert client is not None
        try:
            application = client.get_application(run.application_name)
        except ArgoCDError as exc:
            _reschedule_poll_after_error(db, run, deployment, target.provider, worker_name, exc)
            db.commit()
            return True
        observed, failure_code, failure_message = _validate_application(
            db, run, target, application
        )
    if failure_code:
        _fail_rollout(
            db,
            run,
            deployment,
            code=failure_code,
            message=failure_message
            or f"{provider_name} target no longer matches its registered configuration.",
        )
        db.commit()
        return True

    if target.provider == "argocd":
        _observe_rollout(db, run, target, worker_name)

    now = datetime.now(UTC)
    failed_phase = (observed["operation_phase"] or "").casefold() in {
        "failed",
        "error",
        "stalled",
    }
    degraded_health = (observed["health_status"] or "").casefold() in {"degraded", "missing"}
    deadline = _utc_datetime(run.callback_deadline_at)
    timed_out = deadline is not None and deadline <= now
    if failed_phase or degraded_health or timed_out:
        code = (
            f"{target.provider}_sync_timeout"
            if timed_out
            else f"{target.provider}_health_degraded"
            if degraded_health
            else f"{target.provider}_sync_failed"
        )
        message = (
            f"{provider_name} did not report a healthy rollout before the configured deadline."
            if timed_out
            else f"{provider_name} reported a failed reconciliation or degraded health."
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
        if (
            settings.require_verified_supply_chain
            and run.artifact_uri
            and run.artifact_uri.startswith("oci://")
        ):
            try:
                observed_images = (
                    client.get_live_image_references(
                        observed["target_namespace"], target.application_name
                    )
                    if target.provider == "flux"
                    else observed["images"]
                )
            except FluxError as exc:
                _reschedule_poll_after_error(
                    db, run, deployment, target.provider, worker_name, exc
                )
                db.commit()
                return True
            if not _oci_artifact_digest_is_observed(
                run.artifact_uri, run.artifact_digest, observed_images
            ):
                message = (
                    f"{provider_name} reports the requested revision as healthy, but live image "
                    "evidence does not contain the verified artifact repository and digest."
                )
                _fail_rollout(
                    db,
                    run,
                    deployment,
                    code="gitops_artifact_digest_mismatch",
                    message=message,
                    details={
                        "artifact_digest": run.artifact_digest,
                        "observed_image_count": len(observed_images),
                    },
                )
                db.commit()
                return True
        _finish_healthy_run(db, run, deployment, observed)
        db.commit()
        return True

    next_status = (
        "verifying"
        if target.provider == "argocd"
        and (observed["operation_phase"] or "").casefold() == "succeeded"
        else "syncing"
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
                f"{provider_name} sync operation completed; waiting for health verification."
                if next_status == "verifying"
                else f"{provider_name} is still reconciling the desired revision."
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


def process_next_gitops_rollout_action(db: Session, worker_name: str) -> bool:
    """Dispatch one durable operator action without retrying ambiguous side effects."""
    now = datetime.now(UTC)
    candidate = db.scalar(
        select(GitOpsRolloutAction)
        .where(
            or_(
                (GitOpsRolloutAction.status == "queued")
                & (GitOpsRolloutAction.available_at <= now),
                (GitOpsRolloutAction.status == "dispatching")
                & (GitOpsRolloutAction.lease_expires_at <= now),
            )
        )
        .order_by(GitOpsRolloutAction.available_at, GitOpsRolloutAction.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if candidate is None:
        return False
    run = db.get(GitOpsRun, candidate.gitops_run_id)
    if candidate.status == "dispatching":
        # The provider may have accepted the request before the worker died. Never repeat
        # an at-most-once stage mutation when its result is ambiguous.
        candidate.status = "uncertain"
        candidate.failure_code = "rollout_action_result_unknown"
        candidate.failure_message = (
            "Worker lease expired after dispatch; inspect Argo Rollouts before acting again."
        )
        candidate.completed_at = now
        candidate.lease_owner = None
        candidate.lease_expires_at = None
        if run is not None:
            append_gitops_event(
                db,
                run,
                event_type="rollout_action.uncertain",
                from_status=run.status,
                to_status=run.status,
                message=(
                    "A rollout action may have been sent before its worker lease expired; "
                    "it was not repeated."
                ),
                actor_type="worker",
                actor_id=worker_name,
                details={"action_id": str(candidate.id), "action": candidate.action},
            )
        db.commit()
        return True

    candidate.status = "dispatching"
    candidate.attempt_count += 1
    candidate.lease_owner = worker_name
    candidate.lease_expires_at = now + timedelta(seconds=GITOPS_LEASE_SECONDS)
    candidate.updated_at = now
    if run is not None:
        append_gitops_event(
            db,
            run,
            event_type="rollout_action.dispatching",
            from_status=run.status,
            to_status=run.status,
            message=f"Argo Rollouts {candidate.action} action claimed by a worker.",
            actor_type="worker",
            actor_id=worker_name,
            details={"action_id": str(candidate.id), "action": candidate.action},
        )
    db.commit()
    db.refresh(candidate)
    run = db.get(GitOpsRun, candidate.gitops_run_id)
    deployment = db.get(Deployment, run.deployment_id) if run else None
    environment = db.get(Environment, deployment.environment_id) if deployment else None
    client = _argo_rollouts_client()

    def finish(
        status_value: str,
        event_name: str,
        message: str,
        *,
        code: str | None = None,
    ) -> None:
        candidate.status = status_value
        candidate.failure_code = code
        candidate.failure_message = message if code else None
        candidate.completed_at = datetime.now(UTC)
        candidate.lease_owner = None
        candidate.lease_expires_at = None
        candidate.updated_at = datetime.now(UTC)
        if run is not None:
            append_gitops_event(
                db,
                run,
                event_type=event_name,
                from_status=run.status,
                to_status=run.status,
                message=message,
                actor_type="worker",
                actor_id=worker_name,
                details={
                    "action_id": str(candidate.id),
                    "action": candidate.action,
                    "failure_code": code,
                },
            )
        db.commit()

    if (
        run is None
        or deployment is None
        or environment is None
        or not environment.namespace
        or run.status not in {"syncing", "verifying"}
        or run.action_kind != "deploy"
        or client is None
    ):
        finish(
            "rejected",
            "rollout_action.rejected",
            "The rollout is no longer eligible for manual control or provider credentials "
            "are unavailable.",
            code="rollout_action_no_longer_eligible",
        )
        return True

    try:
        observed = normalize_rollout_info(
            client.get_rollout_info(environment.namespace, run.application_name)
        )
    except ArgoRolloutsError as exc:
        if exc.retryable and candidate.attempt_count < candidate.max_attempts:
            candidate.status = "queued"
            candidate.available_at = datetime.now(UTC) + timedelta(
                seconds=min(30 * candidate.attempt_count, 300)
            )
            candidate.lease_owner = None
            candidate.lease_expires_at = None
            candidate.failure_code = exc.code
            candidate.failure_message = str(exc)
            candidate.updated_at = datetime.now(UTC)
            append_gitops_event(
                db,
                run,
                event_type="rollout_action.retry",
                from_status=run.status,
                to_status=run.status,
                message="Argo Rollouts status could not be checked; the queued action will retry.",
                actor_type="worker",
                actor_id=worker_name,
                details={"action_id": str(candidate.id), "failure_code": exc.code},
            )
            db.commit()
        else:
            finish("rejected", "rollout_action.rejected", str(exc), code=exc.code)
        return True

    requested_index = (
        int(candidate.requested_step)
        if candidate.requested_step is not None and candidate.requested_step.isdigit()
        else None
    )
    phase_matches = (
        str(observed.get("phase") or "").casefold()
        == str(candidate.requested_rollout_status or "").casefold()
    )
    stage_matches = requested_index == observed.get("current_step_index")
    if not phase_matches or not stage_matches:
        finish(
            "superseded",
            "rollout_action.superseded",
            "The rollout stage changed after the request; no provider action was sent.",
        )
        return True
    if candidate.action == "promote" and observed.get("paused") is not True:
        finish(
            "superseded",
            "rollout_action.superseded",
            "The rollout is no longer paused at the requested stage; no provider action was sent.",
        )
        return True
    if candidate.action == "abort" and str(observed.get("phase") or "").casefold() not in {
        "paused",
        "progressing",
    }:
        finish(
            "superseded",
            "rollout_action.superseded",
            "The rollout is no longer active at the requested stage; no provider action was sent.",
        )
        return True

    candidate.dispatched_at = datetime.now(UTC)
    candidate.updated_at = datetime.now(UTC)
    db.add(candidate)
    append_gitops_event(
        db,
        run,
        event_type="rollout_action.sending",
        from_status=run.status,
        to_status=run.status,
        message=f"Argo Rollouts {candidate.action} action sent for the current stage.",
        actor_type="worker",
        actor_id=worker_name,
        details={"action_id": str(candidate.id), "step": candidate.requested_step},
    )
    db.commit()
    try:
        if candidate.action == "promote":
            client.promote_rollout(environment.namespace, run.application_name)
        else:
            client.abort_rollout(environment.namespace, run.application_name)
    except ArgoRolloutsError as exc:
        # A timeout or server error can happen after the control plane accepted the
        # command. Keep the outcome visible and require an operator to inspect state.
        finish(
            "uncertain" if exc.retryable else "rejected",
            "rollout_action.uncertain" if exc.retryable else "rollout_action.rejected",
            (
                "Argo Rollouts did not confirm the action; inspect the live rollout "
                "before retrying."
                if exc.retryable
                else str(exc)
            ),
            code=exc.code,
        )
        return True
    finish(
        "succeeded",
        "rollout_action.succeeded",
        f"Argo Rollouts {candidate.action} action was accepted for the current stage.",
    )
    return True


def process_next_gitops_run(db: Session, worker_name: str) -> bool:
    now = datetime.now(UTC)
    _recover_expired_dispatches(db, now)
    run_id = _claim_queued_run(db, worker_name, now)
    if run_id:
        return _dispatch_claimed_run(db, run_id, worker_name)
    run_id = _claim_due_reconciliation(db, worker_name, now)
    return _poll_claimed_run(db, run_id, worker_name) if run_id else False


def read_target_status(db: Session, target: GitOpsTarget) -> dict:
    if target.provider == "flux":
        latest_run = db.scalar(
            select(GitOpsRun)
            .where(GitOpsRun.target_id == target.id)
            .order_by(GitOpsRun.created_at.desc(), GitOpsRun.id.desc())
            .limit(1)
        )
        if latest_run is None:
            return {
                "application_name": target.application_name,
                "provider": "flux",
                "provider_status": "not_observed",
                "failure_code": None,
                "project": None,
                "sync_status": None,
                "health_status": None,
                "operation_phase": None,
                "revision": None,
                "destination_name": target.expected_cluster_name,
                "destination_server": None,
                "namespace": target.reconciliation_namespace,
                "run_status": None,
            }
        return {
            "application_name": target.application_name,
            "provider": "flux",
            "provider_status": "observed",
            "failure_code": latest_run.failure_code,
            "project": None,
            "sync_status": latest_run.sync_status,
            "health_status": latest_run.health_status,
            "operation_phase": latest_run.operation_phase,
            "revision": latest_run.observed_revision,
            "destination_name": latest_run.destination_name,
            "destination_server": latest_run.destination_server,
            "namespace": target.reconciliation_namespace,
            "run_status": latest_run.status,
        }
    client = _argo_client()
    if client is None:
        return {
            "application_name": target.application_name,
            "provider": "argocd",
            "provider_status": "disconnected",
            "project": None,
            "sync_status": None,
            "health_status": None,
            "operation_phase": None,
            "revision": None,
            "destination_name": None,
            "destination_server": None,
            "namespace": None,
        }
    try:
        application = client.get_application(target.application_name)
    except ArgoCDError as exc:
        return {
            "application_name": target.application_name,
            "provider": "argocd",
            "provider_status": "unavailable",
            "failure_code": exc.code,
            "project": None,
            "sync_status": None,
            "health_status": None,
            "operation_phase": None,
            "revision": None,
            "destination_name": None,
            "destination_server": None,
            "namespace": None,
        }
    return {
        "application_name": target.application_name,
        "provider": "argocd",
        "provider_status": "connected",
        **_extract_app_status(application),
    }
