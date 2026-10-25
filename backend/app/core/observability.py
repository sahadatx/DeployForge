from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.models import (
    ServiceLevelObjective,
    SloAlert,
    SloEvaluation,
    TelemetrySample,
    User,
)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def evaluate_slo(
    db: Session,
    slo: ServiceLevelObjective,
    *,
    now: datetime | None = None,
    actor: User | None = None,
) -> SloEvaluation:
    evaluated_at = as_utc(now or datetime.now(UTC))
    window_start = evaluated_at - timedelta(hours=slo.window_hours)
    query = select(TelemetrySample).where(
        TelemetrySample.workspace_id == slo.workspace_id,
        TelemetrySample.service_id == slo.service_id,
        TelemetrySample.observed_at >= window_start,
        TelemetrySample.observed_at <= evaluated_at,
    )
    if slo.environment_id is not None:
        query = query.where(TelemetrySample.environment_id == slo.environment_id)
    samples = db.scalars(query.order_by(TelemetrySample.observed_at)).all()

    total_events = 0
    good_events = 0
    for sample in samples:
        if slo.indicator == "availability":
            total_events += sample.request_count
            good_events += sample.request_count - sample.error_count
        else:
            bucket = sample.latency_bucket_counts.get(str(slo.latency_threshold_ms))
            if bucket is not None:
                total_events += sample.request_count
                good_events += bucket

    attainment = good_events / total_events if total_events else None
    if attainment is None:
        status = "no_data"
    elif attainment < slo.objective:
        status = "breaching"
    else:
        status = "healthy"
    error_budget_remaining = None
    burn_rate = None
    if attainment is not None:
        burn_rate = (1 - attainment) / (1 - slo.objective)
        error_budget_remaining = max(0.0, min(1.0, 1 - burn_rate))

    evaluation = SloEvaluation(
        workspace_id=slo.workspace_id,
        slo_id=slo.id,
        status=status,
        window_start=window_start,
        window_end=evaluated_at,
        total_events=total_events,
        good_events=good_events,
        attainment=attainment,
        error_budget_remaining=error_budget_remaining,
        burn_rate=burn_rate,
        created_at=evaluated_at,
    )
    db.add(evaluation)
    db.flush()
    slo.last_evaluated_at = evaluated_at

    active_alert = db.scalar(
        select(SloAlert)
        .where(
            SloAlert.slo_id == slo.id,
            SloAlert.status.in_(("firing", "acknowledged")),
        )
        .order_by(SloAlert.created_at.desc())
    )
    if status == "breaching" and active_alert is None:
        percent = attainment * 100 if attainment is not None else 0
        alert = SloAlert(
            workspace_id=slo.workspace_id,
            slo_id=slo.id,
            evaluation_id=evaluation.id,
            status="firing",
            summary=(
                f"{slo.name} is below its {slo.objective:.2%} objective "
                f"({percent:.2f}% observed)"
            ),
            details={
                "indicator": slo.indicator,
                "objective": slo.objective,
                "attainment": attainment,
                "error_budget_remaining": error_budget_remaining,
                "burn_rate": burn_rate,
                "window_hours": slo.window_hours,
            },
            created_at=evaluated_at,
        )
        db.add(alert)
        db.flush()
        record_audit(
            db,
            actor=actor,
            action="slo.alert.fired",
            resource_type="slo_alert",
            resource_id=alert.id,
            workspace_id=slo.workspace_id,
            details={"slo_id": str(slo.id), "evaluation_id": str(evaluation.id)},
        )
    elif status == "healthy" and active_alert is not None:
        active_alert.status = "resolved"
        active_alert.resolved_at = evaluated_at
        record_audit(
            db,
            actor=actor,
            action="slo.alert.auto_resolved",
            resource_type="slo_alert",
            resource_id=active_alert.id,
            workspace_id=slo.workspace_id,
            details={"slo_id": str(slo.id), "evaluation_id": str(evaluation.id)},
        )
    return evaluation


def evaluate_due_slos(db: Session, *, now: datetime | None = None) -> int:
    evaluated_at = as_utc(now or datetime.now(UTC))
    due_before = evaluated_at - timedelta(seconds=60)
    objectives = db.scalars(
        select(ServiceLevelObjective).where(
            ServiceLevelObjective.enabled.is_(True),
            (ServiceLevelObjective.last_evaluated_at.is_(None))
            | (ServiceLevelObjective.last_evaluated_at <= due_before),
        )
    ).all()
    for objective in objectives:
        evaluate_slo(db, objective, now=evaluated_at)
    if objectives:
        db.commit()
    return len(objectives)


def get_slo_evaluation(
    db: Session, workspace_id: UUID, slo_id: UUID
) -> SloEvaluation | None:
    return db.scalar(
        select(SloEvaluation)
        .where(
            SloEvaluation.workspace_id == workspace_id,
            SloEvaluation.slo_id == slo_id,
        )
        .order_by(SloEvaluation.created_at.desc())
    )
