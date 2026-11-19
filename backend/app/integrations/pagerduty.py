"""Durable, deduplicated PagerDuty Events API delivery for SLO alert transitions."""

import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Incident, SloAlert

logger = logging.getLogger("deployforge.pagerduty")
PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"
RETRY_INTERVAL = timedelta(seconds=60)


def _send_event(alert: SloAlert, action: str) -> bool:
    routing_key = settings.pagerduty_routing_key
    if routing_key is None:
        return False
    payload = {
        "routing_key": routing_key.get_secret_value(),
        "event_action": action,
        "dedup_key": f"deployforge-slo-{alert.id}",
        "payload": {
            "summary": alert.summary,
            "source": "DeployForge",
            "severity": "error",
            "component": str(alert.slo_id),
            "class": "SLO alert",
            "custom_details": {
                "alert_id": str(alert.id),
                "workspace_id": str(alert.workspace_id),
                "slo_id": str(alert.slo_id),
                "status": alert.status,
                **(alert.details or {}),
            },
        },
    }
    try:
        response = httpx.post(
            PAGERDUTY_EVENTS_URL,
            json=payload,
            timeout=httpx.Timeout(4.0, connect=2.0),
        )
        response.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        logger.warning(
            "pagerduty_delivery_failed",
            extra={"alert_id": str(alert.id), "action": action, "error_type": type(exc).__name__},
        )
        return False


def dispatch_due_slo_alert_notifications(
    db: Session, *, now: datetime | None = None, limit: int = 1
) -> int:
    """Retry unacknowledged external deliveries; PagerDuty deduplicates by alert ID."""
    if not settings.pagerduty_configured:
        return 0
    current = now or datetime.now(UTC)
    retry_before = current - RETRY_INTERVAL
    due = db.scalars(
        select(SloAlert)
        .where(
            or_(
                (
                    SloAlert.status.in_(("firing", "acknowledged"))
                    & SloAlert.pagerduty_triggered_at.is_(None)
                ),
                (
                    (SloAlert.status == "resolved")
                    & SloAlert.resolved_at.is_not(None)
                    & SloAlert.pagerduty_resolved_notified_at.is_(None)
                    & SloAlert.pagerduty_triggered_at.is_not(None)
                ),
            ),
            or_(
                SloAlert.pagerduty_last_attempt_at.is_(None),
                SloAlert.pagerduty_last_attempt_at <= retry_before,
            ),
        )
        .order_by(SloAlert.created_at, SloAlert.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()

    delivered = 0
    for alert in due:
        action = "trigger" if alert.status in {"firing", "acknowledged"} else "resolve"
        alert.pagerduty_last_attempt_at = current
        db.commit()
        if _send_event(alert, action):
            if action == "trigger":
                alert.pagerduty_triggered_at = current
            else:
                alert.pagerduty_resolved_notified_at = current
            db.commit()
            delivered += 1
    return delivered


def _send_incident_event(incident: Incident, action: str) -> bool:
    routing_key = settings.pagerduty_routing_key
    if routing_key is None:
        return False
    severity = {"sev0": "critical", "sev1": "error", "sev2": "warning", "sev3": "info"}.get(
        incident.severity, "error"
    )
    payload = {
        "routing_key": routing_key.get_secret_value(),
        "event_action": action,
        "dedup_key": f"deployforge-incident-{incident.id}",
        "payload": {
            "summary": incident.title,
            "source": "DeployForge",
            "severity": severity,
            "component": str(incident.service_id or incident.workspace_id),
            "class": "Incident",
            "custom_details": {
                "incident_id": str(incident.id),
                "workspace_id": str(incident.workspace_id),
                "service_id": str(incident.service_id) if incident.service_id else None,
                "environment_id": str(incident.environment_id) if incident.environment_id else None,
                "source_type": incident.source_type,
                "source_id": str(incident.source_id) if incident.source_id else None,
                "severity": incident.severity,
                "status": incident.status,
                "assigned_to": str(incident.assigned_to) if incident.assigned_to else None,
                "escalation_level": incident.escalation_level,
                "description": (incident.description or "")[:3000],
            },
        },
    }
    try:
        response = httpx.post(
            PAGERDUTY_EVENTS_URL,
            json=payload,
            timeout=httpx.Timeout(4.0, connect=2.0),
        )
        response.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        logger.warning(
            "pagerduty_incident_delivery_failed",
            extra={
                "incident_id": str(incident.id),
                "action": action,
                "error_type": type(exc).__name__,
            },
        )
        return False


def dispatch_due_incident_notifications(
    db: Session, *, now: datetime | None = None, limit: int = 1
) -> int:
    """Deliver incident trigger/resolve events; PagerDuty owns external on-call escalation."""
    if not settings.pagerduty_configured:
        return 0
    current = now or datetime.now(UTC)
    retry_before = current - RETRY_INTERVAL
    due = db.scalars(
        select(Incident)
        .where(
            or_(
                (
                    Incident.status.in_(("open", "investigating", "mitigated"))
                    & Incident.pagerduty_triggered_at.is_(None)
                ),
                (
                    Incident.status.in_(("resolved", "closed"))
                    & Incident.resolved_at.is_not(None)
                    & Incident.pagerduty_triggered_at.is_not(None)
                    & Incident.pagerduty_resolved_notified_at.is_(None)
                ),
            ),
            or_(
                Incident.pagerduty_last_attempt_at.is_(None),
                Incident.pagerduty_last_attempt_at <= retry_before,
                (
                    Incident.status.in_(("resolved", "closed"))
                    & Incident.resolved_at.is_not(None)
                    & (Incident.resolved_at >= Incident.pagerduty_last_attempt_at)
                ),
            ),
        )
        .order_by(Incident.created_at, Incident.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()

    delivered = 0
    for incident in due:
        action = (
            "trigger" if incident.status in {"open", "investigating", "mitigated"} else "resolve"
        )
        incident.pagerduty_last_attempt_at = current
        db.commit()
        if _send_incident_event(incident, action):
            if action == "trigger":
                incident.pagerduty_triggered_at = current
            else:
                incident.pagerduty_resolved_notified_at = current
            db.commit()
            delivered += 1
    return delivered
