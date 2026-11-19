from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import get_current_identity, get_db
from app.core.config import settings
from app.models import WorkerHeartbeat

router = APIRouter()


@router.get("/health/live", summary="Liveness probe")
def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready", summary="Readiness probe")
def readiness(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ready", "database": "healthy"}


@router.get("/system/status", summary="Current control plane component status")
def system_status(
    _identity=Depends(get_current_identity), db: Session = Depends(get_db)
) -> dict[str, object]:
    db.execute(text("SELECT 1"))
    heartbeats = db.scalars(select(WorkerHeartbeat)).all()
    now = datetime.now(UTC)
    worker_status = "starting"
    last_seen_at = None
    active_workers = 0
    normalized_seen = []
    for heartbeat in heartbeats:
        seen = heartbeat.last_seen_at
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=UTC)
        normalized_seen.append(seen)
        if (now - seen).total_seconds() <= settings.worker_heartbeat_stale_after_seconds:
            active_workers += 1
    if normalized_seen:
        last_seen_at = max(normalized_seen)
        worker_status = "healthy" if active_workers else "stale"
    return {
        "api": "healthy",
        "database": "healthy",
        "worker": worker_status,
        "active_workers": active_workers,
        "worker_last_seen_at": last_seen_at,
        "checked_at": now,
    }
