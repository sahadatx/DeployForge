import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_identity, get_db, require_csrf
from app.core.audit import record_audit
from app.core.authorization import require_workspace_access
from app.core.observability import as_utc, evaluate_slo, get_slo_evaluation
from app.models import (
    Environment,
    Service,
    ServiceLevelObjective,
    SloAlert,
    SloEvaluation,
    TelemetrySample,
    TelemetrySource,
    User,
    UserSession,
)
from app.schemas.core import (
    ServiceLevelObjectiveContract,
    ServiceLevelObjectiveCreate,
    SloAlertContract,
    SloEvaluationContract,
    TelemetryBatchIn,
    TelemetryBatchResult,
    TelemetrySourceContract,
    TelemetrySourceCreate,
    TelemetrySourceCreated,
    TelemetrySourcePatch,
)

router = APIRouter()
OBSERVABILITY_ADMIN_ROLES = {"workspace_admin", "platform_engineer"}


def _load_slo(db: Session, workspace_id: UUID, slo_id: UUID) -> ServiceLevelObjective:
    slo = db.scalar(
        select(ServiceLevelObjective).where(
            ServiceLevelObjective.workspace_id == workspace_id,
            ServiceLevelObjective.id == slo_id,
        )
    )
    if slo is None:
        raise HTTPException(status_code=404, detail="Service level objective not found")
    return slo


def _load_source(db: Session, workspace_id: UUID, source_id: UUID) -> TelemetrySource:
    source = db.scalar(
        select(TelemetrySource).where(
            TelemetrySource.workspace_id == workspace_id,
            TelemetrySource.id == source_id,
        )
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Telemetry source not found")
    return source


def _source_token(authorization: str | None, db: Session) -> TelemetrySource:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Telemetry source token required")
    raw_token = authorization[7:].strip()
    if not raw_token or len(raw_token) > 200:
        raise HTTPException(status_code=401, detail="Invalid telemetry source token")
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    source = db.scalar(select(TelemetrySource).where(TelemetrySource.token_hash == token_hash))
    if (
        source is None
        or not hmac.compare_digest(source.token_hash, token_hash)
        or not source.enabled
    ):
        raise HTTPException(status_code=401, detail="Invalid or disabled telemetry source")
    return source


@router.post(
    "/workspaces/{workspace_id}/telemetry-sources",
    response_model=TelemetrySourceCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a service-scoped telemetry ingestion credential",
)
def create_telemetry_source(
    workspace_id: UUID,
    payload: TelemetrySourceCreate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=OBSERVABILITY_ADMIN_ROLES)
    service = db.scalar(
        select(Service).where(
            Service.workspace_id == workspace_id, Service.id == payload.service_id
        )
    )
    if service is None:
        raise HTTPException(status_code=422, detail="Service must belong to this workspace")
    if payload.environment_id is not None:
        environment = db.scalar(
            select(Environment).where(
                Environment.workspace_id == workspace_id,
                Environment.id == payload.environment_id,
            )
        )
        if environment is None:
            raise HTTPException(status_code=422, detail="Environment must belong to this workspace")

    raw_token = f"dfm_{secrets.token_urlsafe(36)}"
    source = TelemetrySource(
        workspace_id=workspace_id,
        service_id=service.id,
        environment_id=payload.environment_id,
        name=payload.name,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        token_prefix=raw_token[:12],
        created_by=actor.id,
    )
    db.add(source)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action="telemetry_source.created",
        resource_type="telemetry_source",
        resource_id=source.id,
        workspace_id=workspace_id,
        details={
            "service_id": str(source.service_id),
            "environment_id": str(source.environment_id) if source.environment_id else None,
            "token_prefix": source.token_prefix,
        },
    )
    db.commit()
    db.refresh(source)
    return {**TelemetrySourceContract.model_validate(source).model_dump(), "token": raw_token}


@router.get(
    "/workspaces/{workspace_id}/telemetry-sources",
    response_model=list[TelemetrySourceContract],
    summary="List telemetry sources without revealing credentials",
)
def list_telemetry_sources(
    workspace_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[TelemetrySource]:
    require_workspace_access(db, identity[0], workspace_id)
    return list(
        db.scalars(
            select(TelemetrySource)
            .where(TelemetrySource.workspace_id == workspace_id)
            .order_by(TelemetrySource.created_at.desc())
        ).all()
    )


@router.patch(
    "/workspaces/{workspace_id}/telemetry-sources/{source_id}",
    response_model=TelemetrySourceContract,
    summary="Enable or revoke a telemetry ingestion credential",
)
def update_telemetry_source(
    workspace_id: UUID,
    source_id: UUID,
    payload: TelemetrySourcePatch,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> TelemetrySource:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=OBSERVABILITY_ADMIN_ROLES)
    source = _load_source(db, workspace_id, source_id)
    if source.enabled != payload.enabled:
        source.enabled = payload.enabled
        record_audit(
            db,
            actor=actor,
            action="telemetry_source.enabled" if source.enabled else "telemetry_source.revoked",
            resource_type="telemetry_source",
            resource_id=source.id,
            workspace_id=workspace_id,
            details={"service_id": str(source.service_id)},
        )
        db.commit()
        db.refresh(source)
    return source


@router.post(
    "/telemetry/samples",
    response_model=TelemetryBatchResult,
    summary="Ingest bounded, idempotent service request aggregates",
)
def ingest_telemetry(
    payload: TelemetryBatchIn,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> TelemetryBatchResult:
    source = _source_token(authorization, db)
    now = datetime.now(UTC)
    accepted = 0
    duplicates = 0
    for sample in payload.samples:
        observed_at = sample.observed_at.astimezone(UTC)
        if observed_at > now + timedelta(minutes=5):
            raise HTTPException(
                status_code=422, detail="Telemetry timestamp is too far in the future"
            )
        if observed_at < now - timedelta(days=31):
            raise HTTPException(
                status_code=422, detail="Telemetry data older than 31 days is rejected"
            )
        existing = db.scalar(
            select(TelemetrySample).where(
                TelemetrySample.source_id == source.id,
                TelemetrySample.idempotency_key == sample.idempotency_key,
            )
        )
        if existing is not None:
            same_payload = (
                as_utc(existing.observed_at) == observed_at
                and existing.interval_seconds == sample.interval_seconds
                and existing.request_count == sample.request_count
                and existing.error_count == sample.error_count
                and existing.latency_bucket_counts == sample.latency_bucket_counts
            )
            if not same_payload:
                raise HTTPException(
                    status_code=409,
                    detail="An idempotency key cannot be reused with different telemetry data",
                )
            duplicates += 1
            continue
        db.add(
            TelemetrySample(
                workspace_id=source.workspace_id,
                source_id=source.id,
                service_id=source.service_id,
                environment_id=source.environment_id,
                idempotency_key=sample.idempotency_key,
                observed_at=observed_at,
                interval_seconds=sample.interval_seconds,
                request_count=sample.request_count,
                error_count=sample.error_count,
                latency_bucket_counts=sample.latency_bucket_counts,
            )
        )
        accepted += 1
    source.last_seen_at = now
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Concurrent telemetry delivery conflict; retry the batch with the same keys",
        ) from exc
    return TelemetryBatchResult(accepted=accepted, duplicates=duplicates)


@router.post(
    "/workspaces/{workspace_id}/slos",
    response_model=ServiceLevelObjectiveContract,
    status_code=status.HTTP_201_CREATED,
    summary="Define an availability or latency objective",
)
def create_slo(
    workspace_id: UUID,
    payload: ServiceLevelObjectiveCreate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ServiceLevelObjective:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=OBSERVABILITY_ADMIN_ROLES)
    service = db.scalar(
        select(Service).where(
            Service.workspace_id == workspace_id, Service.id == payload.service_id
        )
    )
    if service is None:
        raise HTTPException(status_code=422, detail="Service must belong to this workspace")
    if payload.environment_id is not None:
        environment = db.scalar(
            select(Environment).where(
                Environment.workspace_id == workspace_id,
                Environment.id == payload.environment_id,
            )
        )
        if environment is None:
            raise HTTPException(status_code=422, detail="Environment must belong to this workspace")
    slo = ServiceLevelObjective(
        workspace_id=workspace_id,
        service_id=payload.service_id,
        environment_id=payload.environment_id,
        slug=payload.slug,
        name=payload.name,
        indicator=payload.indicator,
        objective=payload.objective,
        window_hours=payload.window_hours,
        latency_threshold_ms=payload.latency_threshold_ms,
        created_by=actor.id,
    )
    db.add(slo)
    try:
        db.flush()
        record_audit(
            db,
            actor=actor,
            action="slo.created",
            resource_type="slo",
            resource_id=slo.id,
            workspace_id=workspace_id,
            details={
                "service_id": str(slo.service_id),
                "indicator": slo.indicator,
                "objective": slo.objective,
                "window_hours": slo.window_hours,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="SLO slug already exists in this workspace"
        ) from exc
    db.refresh(slo)
    return slo


@router.get(
    "/workspaces/{workspace_id}/slos",
    response_model=list[ServiceLevelObjectiveContract],
    summary="List workspace service level objectives",
)
def list_slos(
    workspace_id: UUID,
    include_disabled: bool = False,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[ServiceLevelObjective]:
    require_workspace_access(db, identity[0], workspace_id)
    query = select(ServiceLevelObjective).where(ServiceLevelObjective.workspace_id == workspace_id)
    if not include_disabled:
        query = query.where(ServiceLevelObjective.enabled.is_(True))
    return list(db.scalars(query.order_by(ServiceLevelObjective.name)).all())


@router.post(
    "/workspaces/{workspace_id}/slos/{slo_id}/evaluate",
    response_model=SloEvaluationContract,
    status_code=status.HTTP_201_CREATED,
    summary="Evaluate the objective against persisted telemetry",
)
def run_slo_evaluation(
    workspace_id: UUID,
    slo_id: UUID,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> SloEvaluation:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=OBSERVABILITY_ADMIN_ROLES)
    slo = _load_slo(db, workspace_id, slo_id)
    evaluation = evaluate_slo(db, slo, actor=actor)
    db.commit()
    db.refresh(evaluation)
    return evaluation


@router.get(
    "/workspaces/{workspace_id}/slos/{slo_id}/evaluations",
    response_model=list[SloEvaluationContract],
    summary="Read recent objective evaluation history",
)
def list_slo_evaluations(
    workspace_id: UUID,
    slo_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[SloEvaluation]:
    require_workspace_access(db, identity[0], workspace_id)
    _load_slo(db, workspace_id, slo_id)
    return list(
        db.scalars(
            select(SloEvaluation)
            .where(SloEvaluation.workspace_id == workspace_id, SloEvaluation.slo_id == slo_id)
            .order_by(SloEvaluation.created_at.desc())
            .limit(limit)
        ).all()
    )


@router.get(
    "/workspaces/{workspace_id}/slos/{slo_id}/current",
    response_model=SloEvaluationContract | None,
    summary="Read the latest persisted SLO evaluation",
)
def get_current_slo_evaluation(
    workspace_id: UUID,
    slo_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> SloEvaluation | None:
    require_workspace_access(db, identity[0], workspace_id)
    _load_slo(db, workspace_id, slo_id)
    return get_slo_evaluation(db, workspace_id, slo_id)


@router.get(
    "/workspaces/{workspace_id}/alerts",
    response_model=list[SloAlertContract],
    summary="List workspace SLO alerts",
)
def list_slo_alerts(
    workspace_id: UUID,
    status_filter: str | None = Query(
        default=None,
        alias="status",
        pattern="^(firing|acknowledged|resolved)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[SloAlert]:
    require_workspace_access(db, identity[0], workspace_id)
    query = select(SloAlert).where(SloAlert.workspace_id == workspace_id)
    if status_filter:
        query = query.where(SloAlert.status == status_filter)
    return list(db.scalars(query.order_by(SloAlert.created_at.desc()).limit(limit)).all())


@router.post(
    "/workspaces/{workspace_id}/alerts/{alert_id}/acknowledge",
    response_model=SloAlertContract,
    summary="Acknowledge an active SLO alert",
)
def acknowledge_slo_alert(
    workspace_id: UUID,
    alert_id: UUID,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> SloAlert:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=OBSERVABILITY_ADMIN_ROLES)
    alert = db.scalar(
        select(SloAlert).where(
            SloAlert.workspace_id == workspace_id,
            SloAlert.id == alert_id,
        )
    )
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.status == "resolved":
        raise HTTPException(status_code=409, detail="Resolved alerts cannot be acknowledged")
    if alert.status == "firing":
        alert.status = "acknowledged"
        alert.acknowledged_by = actor.id
        alert.acknowledged_at = datetime.now(UTC)
        record_audit(
            db,
            actor=actor,
            action="slo.alert.acknowledged",
            resource_type="slo_alert",
            resource_id=alert.id,
            workspace_id=workspace_id,
            details={"slo_id": str(alert.slo_id)},
        )
        db.commit()
        db.refresh(alert)
    return alert
