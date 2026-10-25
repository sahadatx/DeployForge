"""Durable deployment state transitions shared by the API and worker."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.policies import evaluate_policy_set
from app.models import Deployment, DeploymentEvent, Environment, Service, User

LEASE_SECONDS = 90
TERMINAL_STATES = {"succeeded", "failed", "rejected", "canceled", "rolled_back"}
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"validating", "canceled"},
    "validating": {"policy_check", "failed", "queued"},
    "policy_check": {"awaiting_approval", "failed"},
    "awaiting_approval": {"approved", "rejected", "canceled"},
    "approved": {"building", "failed", "canceled"},
    "building": {
        "policy_check",
        "awaiting_release_approval",
        "ready_to_deploy",
        "deploying",
        "failed",
        "rollback_pending",
    },
    "awaiting_release_approval": {"ready_to_deploy", "rejected", "canceled"},
    "ready_to_deploy": {"deploying", "canceled"},
    "deploying": {"verifying", "failed", "rollback_pending"},
    "verifying": {"succeeded", "failed", "rollback_pending"},
    "rollback_pending": {"rolled_back", "failed"},
    "failed": {"queued"},
    "succeeded": {"rollback_pending"},
}


class DeploymentTransitionConflict(Exception):
    """Raised when another worker or operator changed a release concurrently."""


def append_deployment_event(
    db: Session,
    deployment: Deployment,
    *,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    message: str,
    actor: User | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    details: dict | None = None,
) -> DeploymentEvent:
    current_sequence = db.scalar(
        select(func.max(DeploymentEvent.sequence)).where(
            DeploymentEvent.deployment_id == deployment.id
        )
    )
    event = DeploymentEvent(
        deployment_id=deployment.id,
        workspace_id=deployment.workspace_id,
        sequence=(current_sequence or 0) + 1,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        actor_type=actor_type or ("user" if actor else "system"),
        actor_id=actor_id or (str(actor.id) if actor else None),
        message=message,
        details=dict(details or {}),
    )
    db.add(event)
    # Flush before calculating the next sequence so multiple state changes in one
    # transaction remain ordered even when the Session has autoflush disabled.
    db.flush()
    record_audit(
        db,
        actor=actor,
        action=f"deployment.{event_type}",
        resource_type="deployment",
        resource_id=deployment.id,
        workspace_id=deployment.workspace_id,
        details={
            "from_status": from_status,
            "to_status": to_status,
            "deployment_event_id": str(event.id),
            "event_actor_type": event.actor_type,
            "event_actor_id": event.actor_id,
            **(details or {}),
        },
    )
    return event


def transition_deployment(
    db: Session,
    deployment: Deployment,
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
) -> DeploymentEvent:
    from_status = deployment.status
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
        raise ValueError(f"Invalid deployment transition: {from_status} -> {to_status}")
    now = datetime.now(UTC)
    values: dict = {"status": to_status, "updated_at": now}
    if to_status == "validating" and deployment.started_at is None:
        values["started_at"] = now
    if to_status == "queued":
        values.update(
            available_at=now,
            failure_code=None,
            failure_message=None,
            finished_at=None,
            approved_by=None,
            approved_at=None,
        )
    if to_status == "failed":
        values["failure_code"] = failure_code or "deployment_failed"
        values["failure_message"] = failure_message or message
    if to_status in TERMINAL_STATES:
        values["finished_at"] = now
    if to_status != "validating":
        values["lease_owner"] = None
        values["lease_expires_at"] = None
    if to_status == "approved" and actor is not None:
        values["approved_by"] = actor.id
        values["approved_at"] = now
    result = db.execute(
        update(Deployment)
        .where(Deployment.id == deployment.id, Deployment.status == from_status)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise DeploymentTransitionConflict("Deployment state changed concurrently")
    db.refresh(deployment)
    return append_deployment_event(
        db,
        deployment,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        message=message,
        actor=actor,
        actor_type=actor_type,
        actor_id=actor_id,
        details=details,
    )


def recover_expired_leases(db: Session, *, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    expired = list(
        db.scalars(
            select(Deployment).where(
                Deployment.status == "validating",
                Deployment.lease_expires_at.is_not(None),
                Deployment.lease_expires_at <= now,
            )
        ).all()
    )
    recovered = 0
    for deployment in expired:
        exhausted = deployment.attempt_count >= deployment.max_attempts
        target = "failed" if exhausted else "queued"
        values: dict = {
            "status": target,
            "lease_owner": None,
            "lease_expires_at": None,
            "updated_at": now,
        }
        if exhausted:
            values.update(
                finished_at=now,
                failure_code="worker_lease_exhausted",
                failure_message="Worker lease expired after the maximum number of attempts.",
            )
        else:
            values.update(available_at=now, failure_code=None, failure_message=None)
        result = db.execute(
            update(Deployment)
            .where(
                Deployment.id == deployment.id,
                Deployment.status == "validating",
                Deployment.lease_expires_at <= now,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue
        attempt_count = deployment.attempt_count
        db.refresh(deployment)
        append_deployment_event(
            db,
            deployment,
            event_type="lease.exhausted" if exhausted else "lease.recovered",
            from_status="validating",
            to_status=target,
            message=(
                "Worker lease expired after the maximum number of attempts."
                if exhausted
                else "Expired worker lease recovered; deployment returned to the durable queue."
            ),
            actor_type="worker",
            actor_id="lease-recovery",
            details={"attempt_count": attempt_count},
        )
        recovered += 1
    if recovered:
        db.commit()
    return recovered


def claim_next_deployment(db: Session, worker_name: str) -> UUID | None:
    now = datetime.now(UTC)
    candidate = db.scalar(
        select(Deployment)
        .where(Deployment.status == "queued", Deployment.available_at <= now)
        .order_by(Deployment.created_at, Deployment.id)
        .limit(1)
    )
    if candidate is None:
        return None
    result = db.execute(
        update(Deployment)
        .where(Deployment.id == candidate.id, Deployment.status == "queued")
        .values(
            status="validating",
            attempt_count=Deployment.attempt_count + 1,
            lease_owner=worker_name,
            lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
            started_at=candidate.started_at or now,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.refresh(candidate)
    append_deployment_event(
        db,
        candidate,
        event_type="worker.claimed",
        from_status="queued",
        to_status="validating",
        message="Deployment claimed by a worker for validation.",
        actor_type="worker",
        actor_id=worker_name,
        details={"attempt": candidate.attempt_count, "lease_seconds": LEASE_SECONDS},
    )
    deployment_id = candidate.id
    db.commit()
    return deployment_id


def process_claimed_deployment(db: Session, deployment_id: UUID, worker_name: str) -> bool:
    deployment = db.scalar(
        select(Deployment).where(
            Deployment.id == deployment_id,
            Deployment.status == "validating",
            Deployment.lease_owner == worker_name,
        )
    )
    if deployment is None:
        return False
    service = db.scalar(
        select(Service).where(
            Service.id == deployment.service_id,
            Service.workspace_id == deployment.workspace_id,
        )
    )
    environment = db.scalar(
        select(Environment).where(
            Environment.id == deployment.environment_id,
            Environment.workspace_id == deployment.workspace_id,
        )
    )
    if service is None or environment is None or service.lifecycle == "retired":
        reason = (
            "Catalog service or environment is unavailable."
            if service is None or environment is None
            else "Retired services cannot receive new deployments."
        )
        transition_deployment(
            db,
            deployment,
            "failed",
            event_type="validation.failed",
            message=reason,
            actor_type="worker",
            actor_id=worker_name,
            details={
                "service_id": str(deployment.service_id),
                "environment_id": str(deployment.environment_id),
            },
            failure_code="catalog_validation_failed",
            failure_message=reason,
        )
        db.commit()
        return True

    transition_deployment(
        db,
        deployment,
        "policy_check",
        event_type="validation.passed",
        message="Service and environment references passed validation.",
        actor_type="worker",
        actor_id=worker_name,
        details={"service_slug": service.slug, "environment_slug": environment.slug},
    )
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
        defer_artifact_rules=True,
    )
    if evaluation.decision == "deny":
        reason = "Deployment blocked by the enforced workspace policy set."
        no_production_policy = any(
            result.get("rule_id") == "production-requires-enforced-policy"
            for result in evaluation.results
        )
        transition_deployment(
            db,
            deployment,
            "failed",
            event_type="policy.blocked",
            message=reason,
            actor_type="worker",
            actor_id=worker_name,
            details={
                "policy_evaluation_id": str(evaluation.id),
                "policy_snapshot_sha256": evaluation.policy_snapshot_sha256,
                "enforcement": "fail_closed",
            },
            failure_code=(
                "policy_enforcement_unconfigured" if no_production_policy else "policy_denied"
            ),
            failure_message=reason,
        )
    else:
        transition_deployment(
            db,
            deployment,
            "awaiting_approval",
            event_type="policy.evaluated",
            message=("Policy evaluation passed; deployment is ready for human approval."),
            actor_type="worker",
            actor_id=worker_name,
            details={
                "policy_evaluation_id": str(evaluation.id),
                "policy_snapshot_sha256": evaluation.policy_snapshot_sha256,
                "requires_approval": evaluation.requires_approval,
            },
        )
    db.commit()
    return True


def process_next_deployment(db: Session, worker_name: str) -> bool:
    recover_expired_leases(db)
    deployment_id = claim_next_deployment(db, worker_name)
    return process_claimed_deployment(db, deployment_id, worker_name) if deployment_id else False
