"""Low-cardinality Prometheus metrics for API traffic and control-plane health."""

from datetime import UTC, datetime

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import engine
from app.models import (
    BuildRun,
    Deployment,
    GitHubRepositoryProvisioning,
    GitOpsRun,
    InfrastructureRun,
    SloAlert,
    WorkerHeartbeat,
)

HTTP_REQUESTS = Counter(
    "deployforge_http_requests_total",
    "Completed HTTP requests served by DeployForge API.",
    ("method", "route", "status"),
)
HTTP_REQUEST_DURATION = Histogram(
    "deployforge_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
QUEUE_DEPTH = Gauge(
    "deployforge_job_queue_depth",
    "Durable control-plane job counts by kind and status.",
    ("kind", "status"),
)
QUEUE_OLDEST_AGE = Gauge(
    "deployforge_job_queue_oldest_age_seconds",
    "Age of the oldest job still waiting to be claimed.",
    ("kind",),
)
ACTIVE_WORKERS = Gauge("deployforge_active_workers", "Workers with a fresh heartbeat.")
STALE_WORKERS = Gauge("deployforge_stale_workers", "Workers with an expired heartbeat.")
ACTIVE_SLO_ALERTS = Gauge("deployforge_active_slo_alerts", "SLO alerts not yet resolved.")
DB_POOL_CHECKED_OUT = Gauge(
    "deployforge_db_pool_checked_out", "SQLAlchemy checked-out DB connections."
)


def observe_http_request(
    method: str, route: str, status_code: int, duration_seconds: float
) -> None:
    normalized_route = route if len(route) <= 200 else "unmatched"
    HTTP_REQUESTS.labels(method=method, route=normalized_route, status=str(status_code)).inc()
    HTTP_REQUEST_DURATION.labels(method=method, route=normalized_route).observe(duration_seconds)


def refresh_platform_metrics(db: Session) -> None:
    QUEUE_DEPTH.clear()
    QUEUE_OLDEST_AGE.clear()
    for kind, model in (
        ("deployment", Deployment),
        ("build", BuildRun),
        ("gitops", GitOpsRun),
        ("infrastructure", InfrastructureRun),
        ("repository_provisioning", GitHubRepositoryProvisioning),
    ):
        # A Gauge retains its previous sample when no row is emitted. Reset
        # this label on every scrape so a drained queue does not page forever.
        QUEUE_OLDEST_AGE.labels(kind=kind).set(0)
        for job_status, count in db.execute(
            select(model.status, func.count()).group_by(model.status)
        ).all():
            QUEUE_DEPTH.labels(kind=kind, status=str(job_status)).set(count)
        waiting_statuses = (
            ("queued", "apply_queued")
            if kind == "infrastructure"
            else ("queued",)
        )
        oldest_created_at = db.scalar(
            select(func.min(model.created_at)).where(model.status.in_(waiting_statuses))
        )
        if oldest_created_at is not None:
            if oldest_created_at.tzinfo is None:
                oldest_created_at = oldest_created_at.replace(tzinfo=UTC)
            QUEUE_OLDEST_AGE.labels(kind=kind).set(
                max(0, (datetime.now(UTC) - oldest_created_at).total_seconds())
            )

    now = datetime.now(UTC)
    active = 0
    total = 0
    for heartbeat in db.scalars(select(WorkerHeartbeat)).all():
        total += 1
        seen = heartbeat.last_seen_at
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=UTC)
        active += (now - seen).total_seconds() <= settings.worker_heartbeat_stale_after_seconds
    ACTIVE_WORKERS.set(active)
    STALE_WORKERS.set(total - active)
    ACTIVE_SLO_ALERTS.set(
        db.scalar(
            select(func.count()).select_from(SloAlert).where(
                SloAlert.status.in_(("firing", "acknowledged"))
            )
        )
        or 0
    )
    DB_POOL_CHECKED_OUT.set(max(0, engine.pool.checkedout() - 1))


def generate_metrics(registry: CollectorRegistry = REGISTRY) -> bytes:
    from prometheus_client import generate_latest

    return generate_latest(registry)
