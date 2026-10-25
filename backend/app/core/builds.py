"""Durable CI dispatch and signed supply-chain result processing."""

import hashlib
import hmac
import re
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.core.deployments import transition_deployment
from app.core.gitops import create_gitops_run
from app.core.policies import evaluate_policy_set
from app.integrations.github_actions import (
    DispatchConfiguration,
    GitHubDispatchError,
    dispatch_workflow,
    parse_github_repository,
)
from app.models import (
    BuildArtifact,
    BuildRun,
    BuildRunEvent,
    Deployment,
    Environment,
    Service,
    User,
)
from app.schemas.core import BuildCallbackRequest

BUILD_LEASE_SECONDS = 60
BUILD_TERMINAL_STATES = {"succeeded", "failed", "canceled"}
BUILD_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"dispatching", "canceled"},
    "dispatching": {
        "dispatched",
        "running",
        "succeeded",
        "blocked",
        "queued",
        "failed",
        "canceled",
    },
    "dispatched": {"running", "succeeded", "failed"},
    "running": {"succeeded", "failed"},
    "blocked": {"queued", "canceled"},
    "failed": set(),
    "succeeded": set(),
    "canceled": set(),
}


class BuildRunTransitionConflict(Exception):
    """Raised when an external callback races a worker or another callback."""


def append_build_event(
    db: Session,
    build_run: BuildRun,
    *,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    message: str,
    actor: User | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    delivery_id: str | None = None,
    details: dict | None = None,
) -> BuildRunEvent:
    current_sequence = db.scalar(
        select(func.max(BuildRunEvent.sequence)).where(BuildRunEvent.build_run_id == build_run.id)
    )
    event = BuildRunEvent(
        build_run_id=build_run.id,
        workspace_id=build_run.workspace_id,
        sequence=(current_sequence or 0) + 1,
        delivery_id=delivery_id,
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
        action=f"build_run.{event_type}",
        resource_type="build_run",
        resource_id=build_run.id,
        workspace_id=build_run.workspace_id,
        details={
            "from_status": from_status,
            "to_status": to_status,
            "event_id": str(event.id),
            "event_actor_type": event.actor_type,
            "event_actor_id": event.actor_id,
            **(details or {}),
        },
    )
    return event


def transition_build_run(
    db: Session,
    build_run: BuildRun,
    to_status: str,
    *,
    event_type: str,
    message: str,
    actor: User | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    delivery_id: str | None = None,
    details: dict | None = None,
    failure_code: str | None = None,
    failure_message: str | None = None,
    callback_deadline_at: datetime | None = None,
    external_run_id: str | None = None,
    run_url: str | None = None,
) -> BuildRunEvent:
    from_status = build_run.status
    if to_status not in BUILD_TRANSITIONS.get(from_status, set()):
        raise ValueError(f"Invalid build run transition: {from_status} -> {to_status}")
    now = datetime.now(UTC)
    values: dict = {"status": to_status, "updated_at": now}
    if to_status == "dispatching" and build_run.started_at is None:
        values["started_at"] = now
    if to_status == "queued":
        values.update(available_at=now, failure_code=None, failure_message=None)
    if to_status in BUILD_TERMINAL_STATES:
        values["finished_at"] = now
    if to_status == "blocked":
        values["failure_code"] = failure_code or "build_provider_not_ready"
        values["failure_message"] = failure_message or message
    if to_status == "failed":
        values["failure_code"] = failure_code or "build_failed"
        values["failure_message"] = failure_message or message
    if to_status != "dispatching":
        values["lease_owner"] = None
        values["lease_expires_at"] = None
    if callback_deadline_at is not None:
        values["callback_deadline_at"] = callback_deadline_at
    if external_run_id is not None:
        values["external_run_id"] = external_run_id
    if run_url is not None:
        values["run_url"] = run_url
    result = db.execute(
        update(BuildRun)
        .where(BuildRun.id == build_run.id, BuildRun.status == from_status)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise BuildRunTransitionConflict("Build run state changed concurrently")
    db.refresh(build_run)
    return append_build_event(
        db,
        build_run,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        message=message,
        actor=actor,
        actor_type=actor_type,
        actor_id=actor_id,
        delivery_id=delivery_id,
        details=details,
    )


def create_build_run(
    db: Session,
    deployment: Deployment,
    service: Service,
    *,
    actor: User,
) -> BuildRun:
    attempt_number = (
        db.scalar(
            select(func.max(BuildRun.attempt_number)).where(BuildRun.deployment_id == deployment.id)
        )
        or 0
    ) + 1
    repository_url = service.repository_url
    initial_status = "queued"
    failure_code = None
    failure_message = None
    if not repository_url:
        initial_status = "blocked"
        failure_code = "source_repository_missing"
        failure_message = "Register a source repository for this service before building it."
    else:
        try:
            parse_github_repository(repository_url)
        except GitHubDispatchError as exc:
            initial_status = "blocked"
            failure_code = exc.code
            failure_message = str(exc)
    build_run = BuildRun(
        workspace_id=deployment.workspace_id,
        deployment_id=deployment.id,
        service_id=service.id,
        requested_by=actor.id,
        attempt_number=attempt_number,
        provider="github_actions",
        repository_url=repository_url,
        workflow_path=settings.github_actions_workflow_path,
        source_ref=deployment.source_ref or deployment.version,
        status=initial_status,
        attempt_count=0,
        max_attempts=3,
        failure_code=failure_code,
        failure_message=failure_message,
    )
    db.add(build_run)
    db.flush()
    append_build_event(
        db,
        build_run,
        event_type="queued" if initial_status == "queued" else "blocked",
        from_status=None,
        to_status=initial_status,
        message=(
            "Build request queued for the configured GitHub Actions workflow."
            if initial_status == "queued"
            else failure_message or "Build request is blocked by source configuration."
        ),
        actor=actor,
        details={
            "deployment_id": str(deployment.id),
            "repository_url": repository_url,
            "source_ref": build_run.source_ref,
        },
    )
    return build_run


def verify_callback_signature(
    body: bytes,
    *,
    timestamp: str,
    signature: str,
    secret: str,
    now_epoch: int | None = None,
) -> bool:
    try:
        timestamp_epoch = int(timestamp)
    except (TypeError, ValueError):
        return False
    now_epoch = now_epoch if now_epoch is not None else int(time.time())
    if abs(now_epoch - timestamp_epoch) > 300:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    if not signature.startswith("sha256="):
        return False
    supplied = signature.removeprefix("sha256=")
    return hmac.compare_digest(expected, supplied)


def _same_repository(first: str, second: str) -> bool:
    try:
        return parse_github_repository(first) == parse_github_repository(second)
    except GitHubDispatchError:
        return False


def _ensure_deployment_building(db: Session, deployment: Deployment, build_run: BuildRun) -> None:
    if deployment.status == "approved":
        transition_deployment(
            db,
            deployment,
            "building",
            event_type="build.dispatched",
            message="GitHub Actions accepted the build workflow dispatch.",
            actor_type="worker",
            actor_id="github-actions-adapter",
            details={"build_run_id": str(build_run.id)},
        )


def _fail_build_and_deployment(
    db: Session,
    build_run: BuildRun,
    deployment: Deployment,
    *,
    code: str,
    message: str,
    event_type: str,
    delivery_id: str | None = None,
    details: dict | None = None,
) -> None:
    transition_build_run(
        db,
        build_run,
        "failed",
        event_type=event_type,
        message=message,
        actor_type="system",
        actor_id="github-actions-adapter",
        delivery_id=delivery_id,
        details=details,
        failure_code=code,
        failure_message=message,
    )
    if deployment.status == "approved":
        _ensure_deployment_building(db, deployment, build_run)
    if deployment.status == "building":
        transition_deployment(
            db,
            deployment,
            "failed",
            event_type="build.failed",
            message=message,
            actor_type="system",
            actor_id="github-actions-adapter",
            details={"build_run_id": str(build_run.id), "failure_code": code},
            failure_code=code,
            failure_message=message,
        )


def _recover_build_runs(db: Session, *, now: datetime) -> None:
    expired_dispatches = list(
        db.scalars(
            select(BuildRun).where(
                BuildRun.status == "dispatching",
                BuildRun.lease_expires_at.is_not(None),
                BuildRun.lease_expires_at <= now,
            )
        ).all()
    )
    for build_run in expired_dispatches:
        if build_run.attempt_count < build_run.max_attempts:
            target = "queued"
            values = {
                "status": target,
                "available_at": now,
                "lease_owner": None,
                "lease_expires_at": None,
                "updated_at": now,
            }
        else:
            target = "failed"
            values = {
                "status": target,
                "finished_at": now,
                "failure_code": "build_dispatch_lease_exhausted",
                "failure_message": "Build dispatch worker lease expired after all retries.",
                "lease_owner": None,
                "lease_expires_at": None,
                "updated_at": now,
            }
        result = db.execute(
            update(BuildRun)
            .where(
                BuildRun.id == build_run.id,
                BuildRun.status == "dispatching",
                BuildRun.lease_expires_at <= now,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue
        db.refresh(build_run)
        append_build_event(
            db,
            build_run,
            event_type=(
                "dispatch.lease_recovered" if target == "queued" else "dispatch.lease_exhausted"
            ),
            from_status="dispatching",
            to_status=target,
            message=(
                "Expired dispatch lease returned to the build queue."
                if target == "queued"
                else "Build dispatch worker lease expired after all retries."
            ),
            actor_type="worker",
            actor_id="lease-recovery",
            details={"attempt_count": build_run.attempt_count},
        )
        if target == "failed":
            deployment = db.get(Deployment, build_run.deployment_id)
            if deployment and deployment.status == "approved":
                _ensure_deployment_building(db, deployment, build_run)
            if deployment and deployment.status == "building":
                transition_deployment(
                    db,
                    deployment,
                    "failed",
                    event_type="build.dispatch_lease_exhausted",
                    message="Build dispatch worker lease expired after all retries.",
                    actor_type="worker",
                    actor_id="lease-recovery",
                    details={"build_run_id": str(build_run.id)},
                    failure_code="build_dispatch_lease_exhausted",
                    failure_message="Build dispatch worker lease expired after all retries.",
                )

    timed_out = list(
        db.scalars(
            select(BuildRun).where(
                BuildRun.status.in_({"dispatched", "running"}),
                BuildRun.callback_deadline_at.is_not(None),
                BuildRun.callback_deadline_at <= now,
            )
        ).all()
    )
    for build_run in timed_out:
        deployment = db.get(Deployment, build_run.deployment_id)
        if deployment is None:
            continue
        from_status = build_run.status
        result = db.execute(
            update(BuildRun)
            .where(
                BuildRun.id == build_run.id,
                BuildRun.status.in_({"dispatched", "running"}),
                BuildRun.callback_deadline_at <= now,
            )
            .values(
                status="failed",
                finished_at=now,
                updated_at=now,
                failure_code="build_callback_timeout",
                failure_message=(
                    "The CI workflow did not report a final result before its deadline."
                ),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue
        db.refresh(build_run)
        append_build_event(
            db,
            build_run,
            event_type="callback.timed_out",
            from_status=from_status,
            to_status="failed",
            message="The CI workflow did not report a final result before its deadline.",
            actor_type="worker",
            actor_id="callback-timeout-monitor",
            details={"external_run_id": build_run.external_run_id},
        )
        if deployment.status == "building":
            transition_deployment(
                db,
                deployment,
                "failed",
                event_type="build.callback_timed_out",
                message="The CI workflow did not report a final result before its deadline.",
                actor_type="worker",
                actor_id="callback-timeout-monitor",
                details={"build_run_id": str(build_run.id)},
                failure_code="build_callback_timeout",
                failure_message=(
                    "The CI workflow did not report a final result before its deadline."
                ),
            )
    if expired_dispatches or timed_out:
        db.commit()


def _claim_next_build_run(db: Session, worker_name: str, *, now: datetime) -> UUID | None:
    candidate = db.scalar(
        select(BuildRun)
        .where(BuildRun.status == "queued", BuildRun.available_at <= now)
        .order_by(BuildRun.created_at, BuildRun.id)
        .limit(1)
    )
    if candidate is None:
        return None
    result = db.execute(
        update(BuildRun)
        .where(BuildRun.id == candidate.id, BuildRun.status == "queued")
        .values(
            status="dispatching",
            attempt_count=BuildRun.attempt_count + 1,
            lease_owner=worker_name,
            lease_expires_at=now + timedelta(seconds=BUILD_LEASE_SECONDS),
            started_at=candidate.started_at or now,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.refresh(candidate)
    append_build_event(
        db,
        candidate,
        event_type="worker.claimed",
        from_status="queued",
        to_status="dispatching",
        message="Build dispatch claimed by a worker.",
        actor_type="worker",
        actor_id=worker_name,
        details={"attempt": candidate.attempt_count},
    )
    build_run_id = candidate.id
    db.commit()
    return build_run_id


def _process_claimed_build_run(db: Session, build_run_id: UUID, worker_name: str) -> bool:
    build_run = db.get(BuildRun, build_run_id)
    if (
        build_run is None
        or build_run.status != "dispatching"
        or build_run.lease_owner != worker_name
    ):
        return False
    deployment = db.get(Deployment, build_run.deployment_id)
    if deployment is None or deployment.status != "approved":
        transition_build_run(
            db,
            build_run,
            "canceled",
            event_type="dispatch.canceled",
            message="Build dispatch canceled because the deployment is no longer approved.",
            actor_type="worker",
            actor_id=worker_name,
        )
        db.commit()
        return True
    if not settings.github_actions_builds_configured:
        transition_build_run(
            db,
            build_run,
            "blocked",
            event_type="provider.not_configured",
            message=(
                "GitHub Actions dispatch requires a repository token and an externally "
                "reachable HTTPS callback URL."
            ),
            actor_type="worker",
            actor_id=worker_name,
            failure_code="github_actions_not_configured",
            failure_message="Configure the GitHub Actions provider before retrying this build.",
            details={
                "required_settings": [
                    "GITHUB_ACTIONS_TOKEN",
                    "BUILD_CALLBACK_PUBLIC_URL",
                ]
            },
        )
        db.commit()
        return True

    token = settings.github_actions_token.get_secret_value()
    config = DispatchConfiguration(
        token=token,
        api_url=settings.github_api_url,
        callback_url=settings.build_callback_public_url,
    )
    workflow_path = build_run.workflow_path
    db.commit()  # Release the read transaction before the network request.
    try:
        dispatch_workflow(
            build_run,
            config,
            workflow_path=workflow_path,
        )
    except GitHubDispatchError as exc:
        db.refresh(build_run)
        if build_run.status != "dispatching":
            return True  # A signed runner callback won the race with the HTTP response.
        if exc.retryable and build_run.attempt_count < build_run.max_attempts:
            delay = min(300, 15 * (2 ** (build_run.attempt_count - 1)))
            transition_build_run(
                db,
                build_run,
                "queued",
                event_type="dispatch.retry_scheduled",
                message="GitHub dispatch was temporarily unavailable; retry scheduled.",
                actor_type="worker",
                actor_id=worker_name,
                details={"failure_code": exc.code, "retry_after_seconds": delay},
            )
            build_run.available_at = datetime.now(UTC) + timedelta(seconds=delay)
            db.commit()
            return True
        message = str(exc)
        _fail_build_and_deployment(
            db,
            build_run,
            deployment,
            code=exc.code,
            message=message,
            event_type="dispatch.failed",
            details={"attempt_count": build_run.attempt_count},
        )
        db.commit()
        return True

    db.refresh(build_run)
    if build_run.status == "dispatching":
        transition_build_run(
            db,
            build_run,
            "dispatched",
            event_type="dispatched",
            message=(
                "GitHub Actions accepted the workflow dispatch; waiting for signed build callbacks."
            ),
            actor_type="worker",
            actor_id=worker_name,
            callback_deadline_at=datetime.now(UTC)
            + timedelta(seconds=settings.build_callback_timeout_seconds),
        )
        _ensure_deployment_building(db, deployment, build_run)
        db.commit()
    elif deployment.status == "approved":
        _ensure_deployment_building(db, deployment, build_run)
        db.commit()
    return True


def process_next_build_run(db: Session, worker_name: str) -> bool:
    now = datetime.now(UTC)
    _recover_build_runs(db, now=now)
    build_run_id = _claim_next_build_run(db, worker_name, now=now)
    return _process_claimed_build_run(db, build_run_id, worker_name) if build_run_id else False


def apply_build_callback(
    db: Session,
    build_run: BuildRun,
    payload: BuildCallbackRequest,
    *,
    evidence_verified: bool = False,
    evidence_rejected: bool = False,
) -> bool:
    previous_delivery = db.scalar(
        select(BuildRunEvent).where(
            BuildRunEvent.build_run_id == build_run.id,
            BuildRunEvent.delivery_id == payload.delivery_id,
        )
    )
    if previous_delivery is not None:
        return False
    if not _same_repository(payload.repository_url, build_run.repository_url):
        raise ValueError("Callback repository does not match the registered source repository")
    try:
        parse_github_repository(payload.repository_url)
    except GitHubDispatchError as exc:
        raise ValueError(str(exc)) from exc
    if build_run.external_run_id and payload.external_run_id != build_run.external_run_id:
        raise ValueError("Callback run ID does not match the dispatched GitHub run")
    if build_run.commit_sha and build_run.commit_sha != payload.commit_sha:
        raise ValueError(
            "Callback commit does not match the commit already recorded for this build"
        )
    if (
        build_run.gitops_revision
        and payload.gitops_revision
        and build_run.gitops_revision != payload.gitops_revision
    ):
        raise ValueError("Callback GitOps revision conflicts with the revision already recorded")
    if payload.gitops_revision and payload.status != "succeeded":
        raise ValueError("A GitOps manifest revision may only be reported by a successful build")
    if (
        re.fullmatch(r"[0-9a-f]{40,64}", build_run.source_ref)
        and build_run.source_ref != payload.commit_sha
    ):
        raise ValueError("Built commit does not match the requested immutable source revision")
    if build_run.status not in {"dispatching", "dispatched", "running"}:
        raise ValueError("Build run is not accepting workflow callbacks")
    if payload.status == "succeeded" and not payload.artifacts:
        raise ValueError("A successful build must report at least one artifact and its evidence")
    deployment = db.get(Deployment, build_run.deployment_id)
    if deployment is None:
        raise ValueError("Associated deployment no longer exists")

    if payload.status == "running":
        if build_run.status != "running":
            transition_build_run(
                db,
                build_run,
                "running",
                event_type="callback.running",
                message="GitHub Actions runner started the build workflow.",
                actor_type="github_actions",
                actor_id=payload.external_run_id,
                delivery_id=payload.delivery_id,
                details={
                    "external_run_id": payload.external_run_id,
                    "commit_sha": payload.commit_sha,
                },
                external_run_id=payload.external_run_id,
                run_url=payload.run_url,
                callback_deadline_at=datetime.now(UTC)
                + timedelta(seconds=settings.build_callback_timeout_seconds),
            )
            build_run.commit_sha = payload.commit_sha
        else:
            append_build_event(
                db,
                build_run,
                event_type="callback.heartbeat",
                from_status="running",
                to_status="running",
                message="GitHub Actions runner sent a workflow heartbeat.",
                actor_type="github_actions",
                actor_id=payload.external_run_id,
                delivery_id=payload.delivery_id,
                details={"commit_sha": payload.commit_sha},
            )
        _ensure_deployment_building(db, deployment, build_run)
        return True

    build_run.commit_sha = payload.commit_sha
    build_run.external_run_id = payload.external_run_id
    build_run.run_url = payload.run_url
    if payload.status == "succeeded" and payload.gitops_revision:
        build_run.gitops_revision = payload.gitops_revision

    for evidence in payload.artifacts:
        existing_artifact = db.scalar(
            select(BuildArtifact).where(
                BuildArtifact.build_run_id == build_run.id,
                BuildArtifact.digest == evidence.digest,
            )
        )
        if existing_artifact is None:
            db.add(
                BuildArtifact(
                    workspace_id=build_run.workspace_id,
                    build_run_id=build_run.id,
                    name=evidence.name,
                    artifact_type=evidence.artifact_type,
                    uri=evidence.uri,
                    digest=evidence.digest,
                    sbom_format=evidence.sbom_format,
                    sbom_uri=evidence.sbom_uri,
                    sbom_digest=evidence.sbom_digest,
                    vulnerability_summary=evidence.vulnerability_summary.model_dump(),
                    signature_uri=evidence.signature_uri,
                    signer_identity=evidence.signer_identity,
                    oidc_issuer=evidence.oidc_issuer,
                    provenance_uri=evidence.provenance_uri,
                    provenance_digest=evidence.provenance_digest,
                    evidence_status=(
                        "verified"
                        if evidence_verified
                        else "rejected"
                        if evidence_rejected
                        else "workflow_reported"
                    ),
                )
            )
    db.flush()
    _ensure_deployment_building(db, deployment, build_run)
    vulnerability_counts = {
        severity: sum(getattr(item.vulnerability_summary, severity) for item in payload.artifacts)
        for severity in ("critical", "high", "medium", "low", "unknown")
    }
    if payload.status == "failed":
        message = payload.failure_message or "GitHub Actions reported a build failure."
        _fail_build_and_deployment(
            db,
            build_run,
            deployment,
            code="github_workflow_failed",
            message=message,
            event_type="callback.failed",
            delivery_id=payload.delivery_id,
            details={"external_run_id": payload.external_run_id, "commit_sha": payload.commit_sha},
        )
        return True

    if evidence_rejected:
        message = (
            "Build was rejected because signed artifact or provenance evidence failed verification."
        )
        _fail_build_and_deployment(
            db,
            build_run,
            deployment,
            code="supply_chain_verification_failed",
            message=message,
            event_type="evidence.verification_failed",
            delivery_id=payload.delivery_id,
            details={
                "external_run_id": payload.external_run_id,
                "commit_sha": payload.commit_sha,
                "evidence_verification": "rejected",
            },
        )
        return True

    threshold_breached = (
        vulnerability_counts["critical"] > settings.build_max_critical_vulnerabilities
        or vulnerability_counts["high"] > settings.build_max_high_vulnerabilities
    )
    if threshold_breached:
        message = (
            "Build blocked because reported vulnerability counts exceed the configured threshold."
        )
        _fail_build_and_deployment(
            db,
            build_run,
            deployment,
            code="vulnerability_threshold_exceeded",
            message=message,
            event_type="scan.threshold_exceeded",
            delivery_id=payload.delivery_id,
            details={
                "vulnerability_summary": vulnerability_counts,
                "max_critical": settings.build_max_critical_vulnerabilities,
                "max_high": settings.build_max_high_vulnerabilities,
            },
        )
        return True

    transition_build_run(
        db,
        build_run,
        "succeeded",
        event_type="callback.succeeded",
        message=(
            "Build completed with independently verified artifact signature and provenance."
            if evidence_verified
            else (
                "Build completed and reported artifact, SBOM, scan, signature, "
                "and provenance evidence."
            )
        ),
        actor_type="github_actions",
        actor_id=payload.external_run_id,
        delivery_id=payload.delivery_id,
        details={
            "external_run_id": payload.external_run_id,
            "commit_sha": payload.commit_sha,
            "gitops_revision": payload.gitops_revision,
            "artifact_count": len(payload.artifacts),
            "vulnerability_summary": vulnerability_counts,
            "evidence_verification": "verified" if evidence_verified else "workflow_reported",
        },
        external_run_id=payload.external_run_id,
        run_url=payload.run_url,
    )
    build_run.commit_sha = payload.commit_sha
    service = db.get(Service, deployment.service_id)
    environment = db.get(Environment, deployment.environment_id)
    artifacts = list(
        db.scalars(select(BuildArtifact).where(BuildArtifact.build_run_id == build_run.id)).all()
    )
    if service is None or environment is None or not artifacts:
        message = "Policy evaluation context is incomplete; release is blocked fail-closed."
        transition_deployment(
            db,
            deployment,
            "failed",
            event_type="policy.context_unavailable",
            message=message,
            actor_type="policy",
            actor_id="policy-engine",
            details={"build_run_id": str(build_run.id)},
            failure_code="policy_context_unavailable",
            failure_message=message,
        )
        return True

    vulnerability_counts = {
        severity: sum(int(item.vulnerability_summary.get(severity, 0)) for item in artifacts)
        for severity in ("critical", "high", "medium", "low", "unknown")
    }
    policy_context = {
        "service": {
            "slug": service.slug,
            "lifecycle": service.lifecycle,
            "criticality": service.criticality,
            "tags": service.tags,
            "repository_url": service.repository_url,
        },
        "environment": {
            "slug": environment.slug,
            "type": environment.environment_type,
            "protected": environment.protected,
        },
        "deployment": {"version": deployment.version, "source_ref": deployment.source_ref},
        "artifact": {
            "all_signed": all(
                bool(item.signature_uri and item.signer_identity)
                and (
                    item.evidence_status == "verified"
                    if settings.require_verified_supply_chain
                    else True
                )
                for item in artifacts
            ),
            "all_have_sbom": all(bool(item.sbom_uri and item.sbom_digest) for item in artifacts),
            "all_have_provenance": all(
                bool(item.provenance_uri and item.provenance_digest and item.oidc_issuer)
                for item in artifacts
            ),
            "critical_vulnerabilities": vulnerability_counts["critical"],
            "high_vulnerabilities": vulnerability_counts["high"],
        },
    }
    evaluation = evaluate_policy_set(
        db,
        workspace_id=deployment.workspace_id,
        action="deployment",
        context=policy_context,
        subject_type="deployment",
        subject_id=deployment.id,
        service_id=service.id,
        environment_id=environment.id,
    )
    if evaluation.decision == "deny":
        message = "Built artifact was blocked by the enforced workspace policy set."
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
        return True
    if evaluation.requires_approval:
        transition_deployment(
            db,
            deployment,
            "awaiting_release_approval",
            event_type="policy.release_approval_required",
            message="Artifact policy requires an independent approval before GitOps deployment.",
            actor_type="policy",
            actor_id="policy-engine",
            details={
                "build_run_id": str(build_run.id),
                "policy_evaluation_id": str(evaluation.id),
                "policy_snapshot_sha256": evaluation.policy_snapshot_sha256,
            },
        )
        return True
    if deployment.status == "building":
        transition_deployment(
            db,
            deployment,
            "ready_to_deploy",
            event_type="build.succeeded",
            message="Artifact build completed; release is ready for GitOps deployment.",
            actor_type="system",
            actor_id="github-actions-adapter",
            details={
                "build_run_id": str(build_run.id),
                "commit_sha": payload.commit_sha,
                "policy_evaluation_id": str(evaluation.id),
                "policy_snapshot_sha256": evaluation.policy_snapshot_sha256,
            },
        )
        create_gitops_run(db, deployment, build_run)
    return True
