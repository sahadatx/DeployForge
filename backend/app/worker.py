import logging
import os
import re
import signal
import socket
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from opentelemetry import trace
from sqlalchemy import delete

from app.core.builds import process_next_build_run
from app.core.config import settings
from app.core.deployments import process_next_deployment
from app.core.gitops import process_next_gitops_rollout_action, process_next_gitops_run
from app.core.infrastructure import process_next_infrastructure_run
from app.core.logging import configure_logging
from app.core.observability import evaluate_due_slos
from app.core.repository_provisioning import process_next_repository_provisioning
from app.core.telemetry import configure_telemetry
from app.db.session import SessionLocal, engine
from app.integrations.pagerduty import (
    dispatch_due_incident_notifications,
    dispatch_due_slo_alert_notifications,
)
from app.models import TelemetrySample, WorkerHeartbeat

configure_logging()
logger = logging.getLogger("deployforge.worker")
_worker_prefix = (
    re.sub(
        r"[^A-Za-z0-9_.-]", "-", os.getenv("DEPLOYFORGE_WORKER_NAME", "deployment-worker")
    ).strip("-.")
    or "deployment-worker"
)
_worker_host = re.sub(r"[^A-Za-z0-9_.-]", "-", socket.gethostname()).strip("-.") or "worker"
WORKER_NAME = f"{_worker_prefix[:48]}-{_worker_host[:48]}-{uuid4().hex[:12]}"[:120]
telemetry_providers = configure_telemetry("deployforge-worker", engine=engine)
tracer = trace.get_tracer("deployforge.worker")


def record_heartbeat() -> None:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        heartbeat = db.get(WorkerHeartbeat, WORKER_NAME)
        if heartbeat is None:
            heartbeat = WorkerHeartbeat(worker_name=WORKER_NAME, last_seen_at=now)
            db.add(heartbeat)
        else:
            heartbeat.last_seen_at = now
        # Bound heartbeat-table growth after crashed or replaced worker pods.
        db.execute(
            delete(WorkerHeartbeat).where(
                WorkerHeartbeat.last_seen_at < now - timedelta(days=7),
                WorkerHeartbeat.worker_name != WORKER_NAME,
            )
        )
        db.commit()


def run(stop_event: threading.Event | None = None) -> None:
    owns_stop_event = stop_event is None
    stop_event = stop_event or threading.Event()
    if owns_stop_event and threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, lambda _signum, _frame: stop_event.set())
        signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())
    logger.info("worker_started")
    next_slo_evaluation = 0.0
    next_telemetry_prune = 0.0
    try:
        while not stop_event.is_set():
            worked = False
            try:
                record_heartbeat()
                with SessionLocal() as db:
                    dispatch_due_slo_alert_notifications(db)
                    dispatch_due_incident_notifications(db)
                now_monotonic = time.monotonic()
                if now_monotonic >= next_slo_evaluation:
                    with SessionLocal() as db:
                        evaluated = evaluate_due_slos(db)
                        if evaluated:
                            logger.info("slo_evaluations_completed", extra={"count": evaluated})
                    next_slo_evaluation = now_monotonic + 60
                if now_monotonic >= next_telemetry_prune:
                    cutoff = datetime.now(UTC) - timedelta(days=settings.telemetry_retention_days)
                    with SessionLocal() as db:
                        result = db.execute(
                            delete(TelemetrySample).where(TelemetrySample.observed_at < cutoff)
                        )
                        if result.rowcount:
                            logger.info(
                                "telemetry_samples_pruned", extra={"count": result.rowcount}
                            )
                        db.commit()
                    next_telemetry_prune = now_monotonic + 86_400
                with SessionLocal() as db:
                    with tracer.start_as_current_span("deployforge.worker.deployment.poll") as span:
                        worked = process_next_deployment(db, WORKER_NAME)
                        if worked:
                            span.set_attribute("deployforge.worker.job.kind", "deployment")
                    if not worked:
                        with tracer.start_as_current_span("deployforge.worker.build.poll") as span:
                            worked = process_next_build_run(db, WORKER_NAME)
                            if worked:
                                span.set_attribute("deployforge.worker.job.kind", "build")
                    if not worked:
                        with tracer.start_as_current_span(
                            "deployforge.worker.gitops.rollout_action.poll"
                        ) as span:
                            worked = process_next_gitops_rollout_action(db, WORKER_NAME)
                            if worked:
                                span.set_attribute(
                                    "deployforge.worker.job.kind", "gitops_rollout_action"
                                )
                    if not worked:
                        with tracer.start_as_current_span("deployforge.worker.gitops.poll") as span:
                            worked = process_next_gitops_run(db, WORKER_NAME)
                            if worked:
                                span.set_attribute("deployforge.worker.job.kind", "gitops")
                    if not worked:
                        with tracer.start_as_current_span(
                            "deployforge.worker.repository_provisioning.poll"
                        ) as span:
                            worked = process_next_repository_provisioning(db, WORKER_NAME)
                            if worked:
                                span.set_attribute(
                                    "deployforge.worker.job.kind", "repository_provisioning"
                                )
                    if not worked:
                        with tracer.start_as_current_span(
                            "deployforge.worker.infrastructure.poll"
                        ) as span:
                            worked = process_next_infrastructure_run(db, WORKER_NAME)
                            if worked:
                                span.set_attribute("deployforge.worker.job.kind", "infrastructure")
            except Exception:
                logger.exception("worker_iteration_failed")
            if not worked:
                stop_event.wait(settings.worker_heartbeat_interval_seconds)
    finally:
        logger.info("worker_stopping")
        if telemetry_providers is not None:
            telemetry_providers.shutdown()
        engine.dispose()


if __name__ == "__main__":
    run()
