"""Durable Terraform/OpenTofu plan, approval, apply, and drift orchestration."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.core.policies import evaluate_policy_set
from app.integrations.github_actions import (
    DispatchConfiguration,
    GitHubDispatchError,
    dispatch_workflow_inputs,
    parse_github_repository,
)
from app.models import (
    Environment,
    InfrastructureRun,
    InfrastructureRunEvent,
    InfrastructureTarget,
    User,
)

INFRA_LEASE_SECONDS = 60
INFRA_TERMINAL_STATES = {"succeeded", "drifted", "failed", "blocked", "rejected", "canceled"}
INFRA_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"dispatching", "blocked", "canceled"},
    "apply_queued": {"dispatching", "blocked", "canceled"},
    "dispatching": {
        "dispatched",
        "running",
        "applying",
        "queued",
        "apply_queued",
        "blocked",
        "failed",
        "succeeded",
        "canceled",
    },
    "dispatched": {"running", "applying", "succeeded", "failed", "canceled"},
    "running": {
        "awaiting_approval",
        "succeeded",
        "drifted",
        "blocked",
        "failed",
        "canceled",
    },
    "awaiting_approval": {"apply_queued", "rejected", "canceled"},
    "applying": {"succeeded", "failed", "canceled"},
    "blocked": {"queued", "canceled"},
    "failed": {"queued"},
    "succeeded": set(),
    "drifted": set(),
    "rejected": set(),
    "canceled": set(),
}
QUOTA_FIELDS = {
    "cpu_millis": "quota_cpu_millis",
    "memory_mib": "quota_memory_mib",
    "pods": "quota_pods",
    "persistent_volume_claims": "quota_persistent_volume_claims",
    "services": "quota_services",
}


class InfrastructureTransitionConflict(Exception):
    """Raised when an operator, callback, or worker races a run state transition."""


def append_infrastructure_event(
    db: Session,
    run: InfrastructureRun,
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
) -> InfrastructureRunEvent:
    sequence = (
        db.scalar(
            select(func.max(InfrastructureRunEvent.sequence)).where(
                InfrastructureRunEvent.infrastructure_run_id == run.id
            )
        )
        or 0
    ) + 1
    event = InfrastructureRunEvent(
        infrastructure_run_id=run.id,
        workspace_id=run.workspace_id,
        sequence=sequence,
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
        action=f"infrastructure_run.{event_type}",
        resource_type="infrastructure_run",
        resource_id=run.id,
        workspace_id=run.workspace_id,
        details={
            "from_status": from_status,
            "to_status": to_status,
            "delivery_id": delivery_id,
            "event_id": str(event.id),
            **(details or {}),
        },
    )
    return event


def transition_infrastructure_run(
    db: Session,
    run: InfrastructureRun,
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
    **updates: object,
) -> InfrastructureRunEvent:
    from_status = run.status
    if to_status not in INFRA_TRANSITIONS.get(from_status, set()):
        raise ValueError(f"Invalid infrastructure transition: {from_status} -> {to_status}")
    now = datetime.now(UTC)
    values: dict = {"status": to_status, "updated_at": now, **updates}
    if to_status == "dispatching" and run.started_at is None:
        values["started_at"] = now
    if to_status in {"queued", "apply_queued"}:
        values.update(available_at=now, failure_code=None, failure_message=None)
    if to_status in INFRA_TERMINAL_STATES:
        values.update(finished_at=now, callback_deadline_at=None)
    if to_status == "blocked":
        values["failure_code"] = failure_code or "infrastructure_blocked"
        values["failure_message"] = failure_message or message
    if to_status == "failed":
        values["failure_code"] = failure_code or "infrastructure_failed"
        values["failure_message"] = failure_message or message
    if to_status != "dispatching":
        values["lease_owner"] = None
        values["lease_expires_at"] = None
    result = db.execute(
        update(InfrastructureRun)
        .where(InfrastructureRun.id == run.id, InfrastructureRun.status == from_status)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise InfrastructureTransitionConflict("Infrastructure run state changed concurrently")
    db.refresh(run)
    return append_infrastructure_event(
        db,
        run,
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


def create_infrastructure_run(
    db: Session,
    environment: Environment,
    target: InfrastructureTarget,
    *,
    run_kind: str,
    actor: User,
) -> InfrastructureRun:
    latest_attempt = (
        db.scalar(
            select(func.max(InfrastructureRun.attempt_number)).where(
                InfrastructureRun.environment_id == environment.id
            )
        )
        or 0
    )
    run = InfrastructureRun(
        workspace_id=environment.workspace_id,
        environment_id=environment.id,
        target_id=target.id,
        requested_by=actor.id,
        attempt_number=latest_attempt + 1,
        run_kind=run_kind,
        status="queued",
        source_ref=target.source_ref,
        working_directory=target.working_directory,
        available_at=datetime.now(UTC),
    )
    db.add(run)
    db.flush()
    append_infrastructure_event(
        db,
        run,
        event_type="requested",
        from_status=None,
        to_status="queued",
        message=f"Infrastructure {run_kind} run accepted and added to the durable queue.",
        actor=actor,
        details={
            "environment_id": str(environment.id),
            "target_id": str(target.id),
            "tool": target.tool,
            "source_ref": target.source_ref,
            "working_directory": target.working_directory,
        },
    )
    return run


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _configured_quota_violations(environment: Environment, usage: dict[str, int]) -> list[str]:
    violations: list[str] = []
    for usage_key, limit_field in QUOTA_FIELDS.items():
        limit = getattr(environment, limit_field)
        if limit is None:
            continue
        if usage_key not in usage:
            violations.append(f"{usage_key}:missing")
        elif usage[usage_key] > limit:
            violations.append(f"{usage_key}:{usage[usage_key]}>{limit}")
    return violations


def apply_infrastructure_callback(
    db: Session,
    run: InfrastructureRun,
    payload,
) -> InfrastructureRun:
    duplicate = db.scalar(
        select(InfrastructureRunEvent).where(
            InfrastructureRunEvent.infrastructure_run_id == run.id,
            InfrastructureRunEvent.delivery_id == payload.delivery_id,
        )
    )
    if duplicate is not None:
        return run
    if payload.operation != run.active_operation:
        raise ValueError("Infrastructure callback operation does not match the active run phase")
    if run.status not in {"dispatching", "dispatched", "running", "applying"}:
        raise ValueError("Infrastructure run is not accepting provider callbacks")
    environment = db.get(Environment, run.environment_id)
    if environment is None:
        raise ValueError("Infrastructure environment no longer exists")
    if payload.external_run_id:
        run.external_run_id = payload.external_run_id
    if payload.run_url:
        run.run_url = payload.run_url

    if payload.status == "running":
        next_status = "applying" if payload.operation == "apply" else "running"
        transition_infrastructure_run(
            db,
            run,
            next_status,
            event_type=f"{payload.operation}.running",
            message=f"Infrastructure {payload.operation} is running in the configured workflow.",
            actor_type="provider",
            actor_id="github-actions",
            delivery_id=payload.delivery_id,
            details={"external_run_id": payload.external_run_id, "run_url": payload.run_url},
            external_run_id=payload.external_run_id or run.external_run_id,
            run_url=payload.run_url or run.run_url,
        )
        return run

    if payload.status == "failed":
        if payload.operation == "apply" and payload.plan_digest not in {None, run.plan_digest}:
            raise ValueError("Apply callback plan digest does not match the approved plan")
        transition_infrastructure_run(
            db,
            run,
            "failed",
            event_type=f"{payload.operation}.failed",
            message=(
                "Infrastructure plan failed in the repository workflow; "
                "inspect its protected run logs."
                if payload.operation == "plan"
                else "Infrastructure apply failed in the repository workflow; "
                "inspect its protected run logs."
            ),
            actor_type="provider",
            actor_id="github-actions",
            delivery_id=payload.delivery_id,
            details={"failure_code": payload.failure_code or "infrastructure_workflow_failed"},
            failure_code=payload.failure_code or f"infrastructure_{payload.operation}_failed",
            failure_message=(
                "Infrastructure workflow failed; inspect its protected repository run logs."
            ),
            external_run_id=payload.external_run_id or run.external_run_id,
            run_url=payload.run_url or run.run_url,
        )
        return run

    if payload.operation == "apply":
        if payload.status != "succeeded" or not run.plan_digest:
            raise ValueError("Apply callback must report success for an approved saved plan")
        if payload.plan_digest != run.plan_digest:
            raise ValueError("Applied plan digest does not match the operator-approved plan")
        transition_infrastructure_run(
            db,
            run,
            "succeeded",
            event_type="apply.succeeded",
            message="The approved saved plan was applied successfully.",
            actor_type="provider",
            actor_id="github-actions",
            delivery_id=payload.delivery_id,
            details={"plan_digest": run.plan_digest},
            external_run_id=payload.external_run_id or run.external_run_id,
            run_url=payload.run_url or run.run_url,
        )
        return run

    if payload.status != "planned":
        raise ValueError("Plan callbacks must report running, planned, or failed")
    if not payload.plan_digest or not payload.plan_artifact_uri or payload.plan_has_changes is None:
        raise ValueError(
            "Completed plan must include digest, saved plan artifact, and change status"
        )
    if run.status == "applying":
        raise ValueError("A plan callback cannot replace an apply phase")
    if run.status in {"dispatching", "dispatched"}:
        transition_infrastructure_run(
            db,
            run,
            "running",
            event_type="plan.running",
            message="Infrastructure plan callback arrived before the running notification.",
            actor_type="provider",
            actor_id="github-actions",
            delivery_id=f"{payload.delivery_id}:started",
            external_run_id=payload.external_run_id or run.external_run_id,
            run_url=payload.run_url or run.run_url,
        )
    run.plan_digest = payload.plan_digest
    run.plan_artifact_uri = payload.plan_artifact_uri
    run.plan_has_changes = payload.plan_has_changes
    run.resource_additions = payload.resource_additions
    run.resource_changes = payload.resource_changes
    run.resource_destroys = payload.resource_destroys
    run.quota_usage = payload.quota_usage
    run.external_run_id = payload.external_run_id or run.external_run_id
    run.run_url = payload.run_url or run.run_url
    db.flush()

    policy_evaluation = None
    if run.run_kind == "plan":
        target = db.get(InfrastructureTarget, run.target_id) if run.target_id else None
        policy_context = {
            "environment": {
                "slug": environment.slug,
                "type": environment.environment_type,
                "protected": environment.protected,
            },
            "infrastructure": {
                "tool": target.tool if target is not None else None,
                "source_ref": run.source_ref,
                "plan_has_changes": payload.plan_has_changes,
                "resource_additions": payload.resource_additions,
                "resource_changes": payload.resource_changes,
                "resource_destroys": payload.resource_destroys,
                "quota_usage": payload.quota_usage,
            },
        }
        policy_evaluation = evaluate_policy_set(
            db,
            workspace_id=run.workspace_id,
            action="infrastructure",
            context=policy_context,
            subject_type="infrastructure_run",
            subject_id=run.id,
            environment_id=environment.id,
        )
        if policy_evaluation.decision == "deny":
            message = "Infrastructure plan blocked by the enforced workspace policy set."
            transition_infrastructure_run(
                db,
                run,
                "blocked",
                event_type="plan.blocked_policy",
                message=message,
                actor_type="policy",
                actor_id="policy-engine",
                delivery_id=payload.delivery_id,
                details={
                    "policy_evaluation_id": str(policy_evaluation.id),
                    "policy_snapshot_sha256": policy_evaluation.policy_snapshot_sha256,
                },
                failure_code="policy_denied",
                failure_message=message,
                plan_digest=payload.plan_digest,
                plan_artifact_uri=payload.plan_artifact_uri,
                plan_has_changes=payload.plan_has_changes,
                resource_additions=payload.resource_additions,
                resource_changes=payload.resource_changes,
                resource_destroys=payload.resource_destroys,
                quota_usage=payload.quota_usage,
            )
            return run

    violations = _configured_quota_violations(environment, payload.quota_usage)
    if (
        run.run_kind == "plan"
        and environment.protected
        and not environment.allow_destroy
        and (payload.resource_destroys or 0) > 0
    ):
        transition_infrastructure_run(
            db,
            run,
            "blocked",
            event_type="plan.blocked_destroy",
            message="Protected environment plan includes resource destruction.",
            actor_type="policy",
            actor_id="environment-guard",
            delivery_id=payload.delivery_id,
            details={"resource_destroys": payload.resource_destroys},
            failure_code="protected_environment_destroy_denied",
            failure_message="Destructive plans are not allowed in this protected environment.",
            plan_digest=payload.plan_digest,
            plan_artifact_uri=payload.plan_artifact_uri,
            plan_has_changes=payload.plan_has_changes,
            resource_additions=payload.resource_additions,
            resource_changes=payload.resource_changes,
            resource_destroys=payload.resource_destroys,
            quota_usage=payload.quota_usage,
        )
        return run
    if run.run_kind == "plan" and violations:
        transition_infrastructure_run(
            db,
            run,
            "blocked",
            event_type="plan.blocked_quota",
            message="Infrastructure plan exceeds or omits a configured environment quota.",
            actor_type="policy",
            actor_id="environment-quota",
            delivery_id=payload.delivery_id,
            details={"quota_violations": violations},
            failure_code="environment_quota_exceeded",
            failure_message="Plan is over quota or missing required quota usage evidence.",
            plan_digest=payload.plan_digest,
            plan_artifact_uri=payload.plan_artifact_uri,
            plan_has_changes=payload.plan_has_changes,
            resource_additions=payload.resource_additions,
            resource_changes=payload.resource_changes,
            resource_destroys=payload.resource_destroys,
            quota_usage=payload.quota_usage,
        )
        return run

    if run.run_kind == "drift":
        next_status = "drifted" if payload.plan_has_changes else "succeeded"
        event_type = "drift.detected" if payload.plan_has_changes else "drift.clear"
        message = (
            "Infrastructure drift was detected by a refresh plan."
            if payload.plan_has_changes
            else "Refresh plan found no infrastructure drift."
        )
    elif payload.plan_has_changes:
        next_status = "awaiting_approval"
        event_type = "plan.awaiting_approval"
        message = "Saved infrastructure plan is ready for independent operator approval."
    else:
        next_status = "succeeded"
        event_type = "plan.no_changes"
        message = "Infrastructure plan completed without changes."
    transition_infrastructure_run(
        db,
        run,
        next_status,
        event_type=event_type,
        message=message,
        actor_type="provider",
        actor_id="github-actions",
        delivery_id=payload.delivery_id,
        details={
            "plan_digest": payload.plan_digest,
            "plan_has_changes": payload.plan_has_changes,
            "resource_additions": payload.resource_additions,
            "resource_changes": payload.resource_changes,
            "resource_destroys": payload.resource_destroys,
            "quota_violations": violations,
            "policy_evaluation_id": str(policy_evaluation.id) if policy_evaluation else None,
            "policy_snapshot_sha256": (
                policy_evaluation.policy_snapshot_sha256 if policy_evaluation else None
            ),
            "policy_requires_approval": (
                policy_evaluation.requires_approval if policy_evaluation else False
            ),
        },
        plan_digest=payload.plan_digest,
        plan_artifact_uri=payload.plan_artifact_uri,
        plan_has_changes=payload.plan_has_changes,
        resource_additions=payload.resource_additions,
        resource_changes=payload.resource_changes,
        resource_destroys=payload.resource_destroys,
        quota_usage=payload.quota_usage,
        external_run_id=payload.external_run_id or run.external_run_id,
        run_url=payload.run_url or run.run_url,
    )
    return run


def _recover_expired_dispatches(db: Session, now: datetime) -> None:
    runs = list(
        db.scalars(
            select(InfrastructureRun).where(
                InfrastructureRun.status == "dispatching",
                InfrastructureRun.lease_expires_at.is_not(None),
                InfrastructureRun.lease_expires_at <= now,
            )
        ).all()
    )
    for run in runs:
        queued_status = "apply_queued" if run.active_operation == "apply" else "queued"
        exhausted = run.attempt_count >= run.max_attempts
        next_status = "failed" if exhausted else queued_status
        result = db.execute(
            update(InfrastructureRun)
            .where(
                InfrastructureRun.id == run.id,
                InfrastructureRun.status == "dispatching",
                InfrastructureRun.lease_expires_at <= now,
            )
            .values(
                status=next_status,
                lease_owner=None,
                lease_expires_at=None,
                callback_deadline_at=None,
                available_at=now,
                failure_code="infrastructure_dispatch_lease_exhausted" if exhausted else None,
                failure_message=(
                    "Infrastructure dispatch lease expired after its retry limit."
                    if exhausted
                    else None
                ),
                finished_at=now if exhausted else None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue
        db.refresh(run)
        append_infrastructure_event(
            db,
            run,
            event_type="dispatch.lease_expired",
            from_status="dispatching",
            to_status=next_status,
            message=(
                "Expired infrastructure dispatch lease returned to the queue."
                if not exhausted
                else "Infrastructure dispatch lease expired after the retry limit."
            ),
            actor_type="worker",
            actor_id="lease-recovery",
            details={"attempt_count": run.attempt_count},
        )
    if runs:
        db.commit()


def _expire_provider_runs(db: Session, now: datetime) -> None:
    runs = list(
        db.scalars(
            select(InfrastructureRun).where(
                InfrastructureRun.status.in_({"dispatched", "running", "applying"}),
                InfrastructureRun.callback_deadline_at.is_not(None),
                InfrastructureRun.callback_deadline_at <= now,
            )
        ).all()
    )
    for run in runs:
        try:
            transition_infrastructure_run(
                db,
                run,
                "failed",
                event_type="callback.timed_out",
                message="Infrastructure workflow did not complete before its callback deadline.",
                actor_type="worker",
                actor_id="infrastructure-reconciler",
                failure_code="infrastructure_callback_timeout",
                failure_message=(
                    "Infrastructure workflow did not complete before its callback deadline."
                ),
            )
        except InfrastructureTransitionConflict:
            db.rollback()
    if runs:
        db.commit()


def _claim_run(db: Session, worker_name: str, now: datetime) -> UUID | None:
    candidate = db.scalar(
        select(InfrastructureRun)
        .where(
            InfrastructureRun.status.in_({"queued", "apply_queued"}),
            InfrastructureRun.available_at <= now,
        )
        .order_by(InfrastructureRun.available_at, InfrastructureRun.created_at)
        .limit(1)
    )
    if candidate is None:
        return None
    old_status = candidate.status
    result = db.execute(
        update(InfrastructureRun)
        .where(InfrastructureRun.id == candidate.id, InfrastructureRun.status == old_status)
        .values(
            status="dispatching",
            active_operation="apply" if old_status == "apply_queued" else "plan",
            attempt_count=InfrastructureRun.attempt_count + 1,
            lease_owner=worker_name,
            lease_expires_at=now + timedelta(seconds=INFRA_LEASE_SECONDS),
            started_at=candidate.started_at or now,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.refresh(candidate)
    append_infrastructure_event(
        db,
        candidate,
        event_type=f"{candidate.active_operation}.claimed",
        from_status=old_status,
        to_status="dispatching",
        message=f"Infrastructure {candidate.active_operation} claimed by a worker.",
        actor_type="worker",
        actor_id=worker_name,
        details={"attempt": candidate.attempt_count},
    )
    run_id = candidate.id
    db.commit()
    return run_id


def _dispatch_claimed_run(db: Session, run_id: UUID, worker_name: str) -> bool:
    run = db.get(InfrastructureRun, run_id)
    if run is None or run.status != "dispatching" or run.lease_owner != worker_name:
        return False
    target = db.get(InfrastructureTarget, run.target_id) if run.target_id else None
    environment = db.get(Environment, run.environment_id)
    if not settings.infrastructure_runs_configured:
        transition_infrastructure_run(
            db,
            run,
            "blocked",
            event_type="provider.not_configured",
            message="Configure GitHub Actions and the signed callback endpoint before IaC runs.",
            actor_type="worker",
            actor_id="infrastructure-dispatcher",
            failure_code="infrastructure_provider_not_configured",
            failure_message="GitHub Actions infrastructure workflow is not configured.",
        )
        db.commit()
        return True
    if target is None or not target.enabled or environment is None:
        transition_infrastructure_run(
            db,
            run,
            "blocked",
            event_type="target.unavailable",
            message="Infrastructure target or environment is unavailable.",
            actor_type="worker",
            actor_id="infrastructure-dispatcher",
            failure_code="infrastructure_target_unavailable",
            failure_message="Infrastructure target is missing or disabled.",
        )
        db.commit()
        return True
    if run.active_operation == "apply":
        quota_violations = _configured_quota_violations(environment, run.quota_usage)
        destroy_blocked = (
            environment.protected
            and not environment.allow_destroy
            and (run.resource_destroys or 0) > 0
        )
        if quota_violations or destroy_blocked:
            code = (
                "protected_environment_destroy_denied"
                if destroy_blocked
                else "environment_quota_changed"
            )
            message = (
                "Environment policy changed after approval; a fresh plan is required before apply."
            )
            transition_infrastructure_run(
                db,
                run,
                "blocked",
                event_type="apply.blocked_policy_changed",
                message=message,
                actor_type="policy",
                actor_id="environment-guard",
                details={
                    "quota_violations": quota_violations,
                    "destroy_blocked": destroy_blocked,
                },
                failure_code=code,
                failure_message=message,
            )
            db.commit()
            return True
    try:
        parse_github_repository(target.repository_url)
    except GitHubDispatchError as exc:
        transition_infrastructure_run(
            db,
            run,
            "blocked",
            event_type="target.invalid_repository",
            message=str(exc),
            actor_type="worker",
            actor_id="infrastructure-dispatcher",
            failure_code=exc.code,
            failure_message=str(exc),
        )
        db.commit()
        return True
    assert settings.github_actions_token is not None
    configuration = DispatchConfiguration(
        token=settings.github_actions_token.get_secret_value(),
        api_url=settings.github_api_url,
        callback_url=settings.build_callback_public_url,
    )
    callback_url = (
        f"{settings.build_callback_public_url.rstrip('/')}/api/v1/infrastructure-runs/"
        f"{run.id}/callback"
    )
    quota_limits = {
        key: getattr(environment, field)
        for key, field in QUOTA_FIELDS.items()
        if getattr(environment, field) is not None
    }
    inputs = {
        "infrastructure_run_id": str(run.id),
        "environment_id": str(environment.id),
        "operation": run.active_operation or "plan",
        "run_kind": run.run_kind,
        "tool": target.tool,
        "source_ref": run.source_ref,
        "working_directory": run.working_directory,
        "backend_workspace": target.backend_workspace or "",
        "cluster_name": environment.cluster_name or "",
        "namespace": environment.namespace or "",
        "protected": str(environment.protected).lower(),
        "allow_destroy": str(environment.allow_destroy).lower(),
        "quota_limits": json.dumps(quota_limits, separators=(",", ":")),
        "plan_digest": run.plan_digest or "",
        "plan_artifact_uri": run.plan_artifact_uri or "",
        "callback_url": callback_url,
    }
    try:
        dispatch_workflow_inputs(
            target.repository_url,
            run.source_ref,
            configuration,
            workflow_path=settings.infrastructure_workflow_path,
            inputs=inputs,
        )
    except GitHubDispatchError as exc:
        db.refresh(run)
        if exc.retryable and run.attempt_count < run.max_attempts:
            next_status = "apply_queued" if run.active_operation == "apply" else "queued"
            transition_infrastructure_run(
                db,
                run,
                next_status,
                event_type="dispatch.retry_scheduled",
                message="GitHub Actions is temporarily unavailable; IaC dispatch was rescheduled.",
                actor_type="worker",
                actor_id="infrastructure-dispatcher",
                details={"failure_code": exc.code, "delay_seconds": 20},
                available_at=datetime.now(UTC) + timedelta(seconds=20),
            )
        elif exc.retryable:
            transition_infrastructure_run(
                db,
                run,
                "failed",
                event_type="dispatch.retry_exhausted",
                message="Infrastructure workflow dispatch exhausted its retry limit.",
                actor_type="worker",
                actor_id="infrastructure-dispatcher",
                failure_code=exc.code,
                failure_message="Infrastructure workflow dispatch exhausted its retry limit.",
            )
        else:
            transition_infrastructure_run(
                db,
                run,
                "blocked",
                event_type="dispatch.blocked",
                message=str(exc),
                actor_type="worker",
                actor_id="infrastructure-dispatcher",
                failure_code=exc.code,
                failure_message=str(exc),
            )
        db.commit()
        return True
    now = datetime.now(UTC)
    transition_infrastructure_run(
        db,
        run,
        "dispatched",
        event_type=f"{run.active_operation}.dispatched",
        message=f"Infrastructure {run.active_operation} workflow was accepted by GitHub Actions.",
        actor_type="worker",
        actor_id=worker_name,
        details={"workflow_path": settings.infrastructure_workflow_path},
        callback_deadline_at=now + timedelta(seconds=settings.build_callback_timeout_seconds),
    )
    db.commit()
    return True


def process_next_infrastructure_run(db: Session, worker_name: str) -> bool:
    now = datetime.now(UTC)
    _recover_expired_dispatches(db, now)
    _expire_provider_runs(db, now)
    run_id = _claim_run(db, worker_name, now)
    return _dispatch_claimed_run(db, run_id, worker_name) if run_id else False
