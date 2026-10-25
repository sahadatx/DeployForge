from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text
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
    heartbeat = db.get(WorkerHeartbeat, "deployment-worker")
    now = datetime.now(UTC)
    worker_status = "starting"
    last_seen_at = None
    if heartbeat:
        seen = heartbeat.last_seen_at
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=UTC)
        last_seen_at = seen
        age_seconds = (now - seen).total_seconds()
        worker_status = (
            "healthy" if age_seconds <= settings.worker_heartbeat_stale_after_seconds else "stale"
        )
    return {
        "api": "healthy",
        "database": "healthy",
        "worker": worker_status,
        "worker_last_seen_at": last_seen_at,
        "checked_at": now,
    }
