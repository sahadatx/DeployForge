from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import settings
from app.integrations import pagerduty
from app.models import AuditEvent, Deployment, Incident, IncidentEvent, Service
from tests.auth_helpers import provision_local_user, sign_in


def create_workspace(client: TestClient, email: str = "incident-owner@example.dev"):
    session = sign_in(client, email, "Incident Owner")
    headers = {"X-CSRF-Token": session["csrf_token"]}
    created = client.post(
        "/api/v1/workspaces",
        json={"name": "Incident Workspace", "slug": f"incident-{uuid4().hex[:8]}"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    return session, created.json(), headers


def test_incident_lifecycle_audit_and_independent_postmortem_review(client):
    owner, workspace, headers = create_workspace(client)
    base = f"/api/v1/workspaces/{workspace['id']}"

    opened = client.post(
        f"{base}/incidents",
        json={
            "title": "Checkout latency elevated",
            "description": "Elevated p95 latency affects checkout submissions.",
            "severity": "sev1",
        },
        headers=headers,
    )
    assert opened.status_code == 201, opened.text
    incident_id = opened.json()["id"]
    assert opened.json()["status"] == "open"

    assigned = client.post(
        f"{base}/incidents/{incident_id}/assign",
        json={"assignee_id": owner["user"]["id"], "comment": "Primary responder assigned."},
        headers=headers,
    )
    assert assigned.status_code == 200, assigned.text
    escalated = client.post(
        f"{base}/incidents/{incident_id}/escalate",
        json={"comment": "Requesting senior response coverage."},
        headers=headers,
    )
    assert escalated.status_code == 200, escalated.text
    assert escalated.json()["escalation_level"] == 1

    invalid_transition = client.post(
        f"{base}/incidents/{incident_id}/transition",
        json={"status": "closed", "comment": "Close it"},
        headers=headers,
    )
    assert invalid_transition.status_code == 409
    missing_resolution = client.post(
        f"{base}/incidents/{incident_id}/transition",
        json={"status": "resolved", "comment": "Resolved", "resolution": ""},
        headers=headers,
    )
    assert missing_resolution.status_code == 422

    investigating = client.post(
        f"{base}/incidents/{incident_id}/transition",
        json={"status": "investigating", "comment": "Mitigation work started."},
        headers=headers,
    )
    assert investigating.status_code == 200, investigating.text
    resolved = client.post(
        f"{base}/incidents/{incident_id}/transition",
        json={
            "status": "resolved",
            "comment": "Traffic normalized and error rate returned to baseline.",
            "resolution": "Rolled back the release and drained the affected workers.",
        },
        headers=headers,
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["resolved_at"]

    postmortem = {
        "title": "Checkout latency incident review",
        "summary": "A release increased checkout latency for production customers.",
        "impact": "Checkout submissions were delayed for customers in production.",
        "root_cause": "A connection pool change caused requests to queue under peak load.",
        "detection": "The production availability and latency alert fired after the release.",
        "response": "The responder rolled back the release and drained affected workers.",
        "corrective_actions": ["Add a peak load test for the connection pool change."],
    }
    saved = client.put(
        f"{base}/incidents/{incident_id}/postmortem", json=postmortem, headers=headers
    )
    assert saved.status_code == 200, saved.text
    submitted = client.post(f"{base}/incidents/{incident_id}/postmortem/submit", headers=headers)
    assert submitted.status_code == 200, submitted.text
    self_review = client.post(
        f"{base}/incidents/{incident_id}/postmortem/review",
        json={"decision": "approve", "comment": "Looks good to publish."},
        headers=headers,
    )
    assert self_review.status_code == 403

    provision_local_user(client, "incident-reviewer@example.dev", "Incident Reviewer")
    owner_login = sign_in(client, "incident-owner@example.dev")
    granted = client.post(
        f"{base}/members",
        json={"email": "incident-reviewer@example.dev", "role": "service_owner"},
        headers={"X-CSRF-Token": owner_login["csrf_token"]},
    )
    assert granted.status_code == 201, granted.text
    reviewer = sign_in(client, "incident-reviewer@example.dev")
    reviewed = client.post(
        f"{base}/incidents/{incident_id}/postmortem/review",
        json={"decision": "approve", "comment": "Corrective action is clear and measurable."},
        headers={"X-CSRF-Token": reviewer["csrf_token"]},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "published"
    assert reviewed.json()["reviewer_id"] == reviewer["user"]["id"]

    timeline = client.get(f"{base}/incidents/{incident_id}/events")
    assert timeline.status_code == 200, timeline.text
    assert [event["sequence"] for event in timeline.json()] == list(
        range(1, len(timeline.json()) + 1)
    )
    assert {event["event_type"] for event in timeline.json()} >= {
        "created",
        "assigned",
        "escalated",
        "status_changed",
        "postmortem_submitted",
        "postmortem_reviewed",
    }
    with client.app.state.test_session_factory() as db:
        audit_actions = set(
            db.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.resource_type == "incident",
                    AuditEvent.resource_id == incident_id,
                )
            ).all()
        )
        assert "incident.created" in audit_actions
        assert "incident.status_changed" in audit_actions
        assert "incident.postmortem_reviewed" in audit_actions


def test_failed_deployment_conversion_is_scoped_and_idempotent(client):
    _session, workspace, headers = create_workspace(client, "incident-source@example.dev")
    base = f"/api/v1/workspaces/{workspace['id']}"
    team = client.post(
        f"{base}/teams", json={"slug": "checkout", "name": "Checkout"}, headers=headers
    )
    assert team.status_code == 201, team.text
    service = client.post(
        f"{base}/services",
        json={"slug": "checkout", "name": "Checkout API", "owner_team_id": team.json()["id"]},
        headers=headers,
    )
    assert service.status_code == 201, service.text
    environment = client.post(
        f"{base}/environments",
        json={"slug": "production", "name": "Production", "environment_type": "production"},
        headers=headers,
    )
    assert environment.status_code == 201, environment.text
    deployment_id = uuid4()
    with client.app.state.test_session_factory() as db:
        db.add(
            Deployment(
                id=deployment_id,
                workspace_id=UUID(workspace["id"]),
                service_id=UUID(service.json()["id"]),
                environment_id=UUID(environment.json()["id"]),
                version="2026.11.07.4",
                status="failed",
                failure_message="Health check timed out",
            )
        )
        db.commit()

    payload = {
        "severity": "sev2",
        "source_type": "deployment",
        "source_id": str(deployment_id),
    }
    first = client.post(f"{base}/incidents", json=payload, headers=headers)
    duplicate = client.post(f"{base}/incidents", json=payload, headers=headers)
    assert first.status_code == 201, first.text
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["id"] == first.json()["id"]
    assert first.json()["title"] == "Release 2026.11.07.4 failed"
    assert first.json()["service_id"] == service.json()["id"]
    assert first.json()["environment_id"] == environment.json()["id"]
    assert (
        client.post(
            f"/api/v1/workspaces/{uuid4()}/incidents",
            json=payload,
            headers=headers,
        ).status_code
        == 404
    )
    with client.app.state.test_session_factory() as db:
        assert db.scalar(select(Incident).where(Incident.source_id == deployment_id)) is not None
        assert (
            len(
                db.scalars(
                    select(IncidentEvent).where(
                        IncidentEvent.incident_id == UUID(first.json()["id"])
                    )
                ).all()
            )
            == 1
        )
        assert db.get(Service, UUID(service.json()["id"])) is not None


def test_pagerduty_incident_trigger_and_resolve_are_deduplicated(client, monkeypatch):
    _session, workspace, _headers = create_workspace(client, "incident-pagerduty@example.dev")
    incident = Incident(
        workspace_id=UUID(workspace["id"]),
        title="Database connection pool exhausted",
        description="Production requests are timing out.",
        source_type="manual",
        severity="sev0",
        status="open",
    )
    with client.app.state.test_session_factory() as db:
        db.add(incident)
        db.commit()
        db.refresh(incident)
        monkeypatch.setattr(settings, "pagerduty_routing_key", SecretStr("test-routing-key"))
        actions = []
        monkeypatch.setattr(
            pagerduty,
            "_send_incident_event",
            lambda record, action: actions.append((record.id, action)) or True,
        )
        assert pagerduty.dispatch_due_incident_notifications(db) == 1
        assert actions == [(incident.id, "trigger")]
        db.refresh(incident)
        assert incident.pagerduty_triggered_at is not None
        incident.status = "resolved"
        incident.resolved_at = datetime.now(UTC)
        db.commit()
        assert pagerduty.dispatch_due_incident_notifications(db) == 1
        assert actions[-1] == (incident.id, "resolve")
        db.refresh(incident)
        assert incident.pagerduty_resolved_notified_at is not None


def test_pagerduty_payload_tolerates_an_incident_without_description(
    client, monkeypatch
):
    _session, workspace, _headers = create_workspace(
        client, "incident-pagerduty-empty-description@example.dev"
    )
    incident = Incident(
        workspace_id=UUID(workspace["id"]),
        title="Worker backlog increased",
        description=None,
        source_type="manual",
        severity="sev2",
        status="open",
    )
    with client.app.state.test_session_factory() as db:
        db.add(incident)
        db.commit()
        db.refresh(incident)
        monkeypatch.setattr(settings, "pagerduty_routing_key", SecretStr("test-routing-key"))
        request = {}

        class Response:
            def raise_for_status(self):
                return None

        def send(url, *, json, timeout):
            request.update(url=url, payload=json, timeout=timeout)
            return Response()

        monkeypatch.setattr(pagerduty.httpx, "post", send)
        assert pagerduty._send_incident_event(incident, "trigger")
        details = request["payload"]["payload"]["custom_details"]
        assert details["description"] == ""
        assert request["timeout"].connect == 2.0
