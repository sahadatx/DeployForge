from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.deployments import process_next_deployment, recover_expired_leases
from app.models import Deployment, DeploymentEvent


def sign_in(client: TestClient, email: str, display_name: str) -> dict:
    response = client.post(
        "/api/v1/auth/dev-login",
        json={"email": email, "display_name": display_name},
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_deployment_prerequisites(
    client: TestClient,
    session: dict,
    *,
    environment_type: str = "staging",
) -> tuple[dict, dict, dict]:
    headers = {"X-CSRF-Token": session["csrf_token"]}
    workspace_response = client.post(
        "/api/v1/workspaces",
        json={"name": "Production Platform", "slug": f"platform-{environment_type}"},
        headers=headers,
    )
    assert workspace_response.status_code == 201, workspace_response.text
    workspace = workspace_response.json()

    team_response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/teams",
        json={"slug": "platform", "name": "Platform Engineering"},
        headers=headers,
    )
    assert team_response.status_code == 201, team_response.text
    service_response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/services",
        json={
            "slug": "checkout-api",
            "name": "Checkout API",
            "owner_team_id": team_response.json()["id"],
            "lifecycle": "production",
        },
        headers=headers,
    )
    assert service_response.status_code == 201, service_response.text
    environment_response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/environments",
        json={
            "slug": environment_type,
            "name": environment_type.title(),
            "environment_type": environment_type,
        },
        headers=headers,
    )
    assert environment_response.status_code == 201, environment_response.text
    return workspace, service_response.json(), environment_response.json()


def queue_deployment(
    client: TestClient,
    session: dict,
    workspace_id: str,
    service_id: str,
    environment_id: str,
    *,
    key: str = "release-2026-10-25-1",
    version: str = "sha-abc123",
) -> tuple[object, dict]:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/deployments",
        json={
            "service_id": service_id,
            "environment_id": environment_id,
            "version": version,
        },
        headers={"X-CSRF-Token": session["csrf_token"], "Idempotency-Key": key},
    )
    return response, response.json()


def test_durable_release_idempotency_worker_approval_and_event_history(client):
    requester = sign_in(client, "requester@example.dev", "Release Requester")
    workspace, service, environment = create_deployment_prerequisites(client, requester)
    workspace_id = workspace["id"]

    accepted, payload = queue_deployment(
        client,
        requester,
        workspace_id,
        service["id"],
        environment["id"],
    )
    assert accepted.status_code == 202, accepted.text
    deployment_id = payload["id"]
    assert payload["status"] == "queued"
    assert payload["attempt_count"] == 0
    assert payload["max_attempts"] == 3

    replay, replay_payload = queue_deployment(
        client,
        requester,
        workspace_id,
        service["id"],
        environment["id"],
    )
    assert replay.status_code == 200
    assert replay_payload["id"] == deployment_id
    conflict, _ = queue_deployment(
        client,
        requester,
        workspace_id,
        service["id"],
        environment["id"],
        version="sha-different",
    )
    assert conflict.status_code == 409

    with client.app.state.test_session_factory() as db:
        assert process_next_deployment(db, "deployment-worker-test") is True
    current = client.get(f"/api/v1/workspaces/{workspace_id}/deployments/{deployment_id}")
    assert current.status_code == 200
    assert current.json()["status"] == "awaiting_approval"

    self_approval = client.post(
        f"/api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/approval",
        json={"decision": "approve"},
        headers={"X-CSRF-Token": requester["csrf_token"]},
    )
    assert self_approval.status_code == 403

    approver_client = TestClient(client.app)
    approver = sign_in(approver_client, "approver@example.dev", "Platform Approver")
    membership = client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={"email": approver["user"]["email"], "role": "platform_engineer"},
        headers={"X-CSRF-Token": requester["csrf_token"]},
    )
    assert membership.status_code == 201, membership.text
    approved = approver_client.post(
        f"/api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/approval",
        json={"decision": "approve", "comment": "Change window confirmed"},
        headers={"X-CSRF-Token": approver["csrf_token"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"] == approver["user"]["id"]

    events = client.get(f"/api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/events")
    assert events.status_code == 200
    assert [event["sequence"] for event in events.json()] == [1, 2, 3, 4, 5, 6]
    assert [event["to_status"] for event in events.json()] == [
        "queued",
        "validating",
        "policy_check",
        "awaiting_approval",
        "approved",
        "approved",
    ]
    assert events.json()[4]["details"]["comment"] == "Change window confirmed"
    build_runs = approver_client.get(
        f"/api/v1/workspaces/{workspace_id}/build-runs?deployment_id={deployment_id}"
    )
    assert build_runs.status_code == 200
    assert len(build_runs.json()) == 1
    assert build_runs.json()[0]["status"] == "blocked"
    assert build_runs.json()[0]["failure_code"] == "source_repository_missing"


def test_production_policy_fails_closed_and_retry_limit_is_enforced(client):
    requester = sign_in(client, "prod-requester@example.dev", "Production Requester")
    workspace, service, environment = create_deployment_prerequisites(
        client, requester, environment_type="production"
    )
    workspace_id = workspace["id"]
    created, payload = queue_deployment(
        client,
        requester,
        workspace_id,
        service["id"],
        environment["id"],
        key="prod-release-1",
    )
    assert created.status_code == 202, created.text
    deployment_id = payload["id"]

    for attempt in range(1, 4):
        with client.app.state.test_session_factory() as db:
            assert process_next_deployment(db, "deployment-worker-test") is True
        deployment = client.get(
            f"/api/v1/workspaces/{workspace_id}/deployments/{deployment_id}"
        ).json()
        assert deployment["status"] == "failed"
        assert deployment["failure_code"] == "policy_enforcement_unconfigured"
        assert deployment["attempt_count"] == attempt
        if attempt < 3:
            retry = client.post(
                f"/api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/retry",
                headers={"X-CSRF-Token": requester["csrf_token"]},
            )
            assert retry.status_code == 200, retry.text
            assert retry.json()["deployment"]["status"] == "queued"
        else:
            retry = client.post(
                f"/api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/retry",
                headers={"X-CSRF-Token": requester["csrf_token"]},
            )
            assert retry.status_code == 409


def test_deployment_permissions_workspace_scoping_and_environment_validation(client):
    admin = sign_in(client, "deployment-admin@example.dev", "Deployment Admin")
    workspace, service, environment = create_deployment_prerequisites(client, admin)
    workspace_id = workspace["id"]
    headers = {"X-CSRF-Token": admin["csrf_token"]}

    outsider_client = TestClient(client.app)
    outsider = sign_in(outsider_client, "deployment-outsider@example.dev", "Outsider")
    denied = outsider_client.post(
        f"/api/v1/workspaces/{workspace_id}/deployments",
        json={
            "service_id": service["id"],
            "environment_id": environment["id"],
            "version": "sha-denied",
        },
        headers={"X-CSRF-Token": outsider["csrf_token"]},
    )
    assert denied.status_code == 404

    forbidden_environment = client.post(
        f"/api/v1/workspaces/{workspace_id}/environments",
        json={"slug": "bad-prod", "name": "Bad Prod", "environment_type": "production"},
        headers={"X-CSRF-Token": "invalid"},
    )
    assert forbidden_environment.status_code == 403

    foreign_id = "00000000-0000-0000-0000-000000000000"
    invalid = client.post(
        f"/api/v1/workspaces/{workspace_id}/deployments",
        json={
            "service_id": service["id"],
            "environment_id": foreign_id,
            "version": "sha-invalid-env",
        },
        headers=headers,
    )
    assert invalid.status_code == 422


def test_expired_worker_lease_recovers_or_exhausts_attempts(client):
    requester = sign_in(client, "recovery@example.dev", "Recovery Engineer")
    workspace, service, environment = create_deployment_prerequisites(client, requester)
    response, payload = queue_deployment(
        client,
        requester,
        workspace["id"],
        service["id"],
        environment["id"],
        key="recovery-release-1",
    )
    assert response.status_code == 202

    with client.app.state.test_session_factory() as db:
        deployment = db.get(Deployment, UUID(payload["id"]))
        assert deployment is not None
        deployment.status = "validating"
        deployment.attempt_count = 1
        deployment.lease_owner = "dead-worker"
        deployment.lease_expires_at = datetime.now(UTC) - timedelta(minutes=2)
        db.commit()
        assert recover_expired_leases(db) == 1
        db.refresh(deployment)
        assert deployment.status == "queued"
        assert deployment.lease_owner is None

        deployment.status = "validating"
        deployment.attempt_count = deployment.max_attempts
        deployment.lease_owner = "dead-worker"
        deployment.lease_expires_at = datetime.now(UTC) - timedelta(minutes=2)
        db.commit()
        assert recover_expired_leases(db) == 1
        db.refresh(deployment)
        assert deployment.status == "failed"
        assert deployment.failure_code == "worker_lease_exhausted"
        event_types = list(
            db.scalars(
                select(DeploymentEvent.event_type)
                .where(DeploymentEvent.deployment_id == deployment.id)
                .order_by(DeploymentEvent.sequence)
            ).all()
        )
        assert event_types[-2:] == ["lease.recovered", "lease.exhausted"]
