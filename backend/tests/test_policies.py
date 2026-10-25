from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient


def sign_in(client: TestClient, email: str, name: str) -> dict:
    response = client.post("/api/v1/auth/dev-login", json={"email": email, "display_name": name})
    assert response.status_code == 200, response.text
    return response.json()


def create_workspace(client: TestClient, owner: dict, slug: str) -> dict:
    response = client.post(
        "/api/v1/workspaces",
        json={"name": f"{slug.title()} Workspace", "slug": slug},
        headers={"X-CSRF-Token": owner["csrf_token"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_policy(client: TestClient, owner: dict, workspace_id: str, *, action="infrastructure"):
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/policies",
        json={
            "slug": "safe-change",
            "name": "Safe change policy",
            "actions": [action],
            "enforcement_mode": "enforce",
            "rules": [
                {
                    "id": "deny-large-destroy",
                    "name": "Deny large destructive plans",
                    "description": "Plans destroying more than one resource need redesign.",
                    "field": "infrastructure.resource_destroys",
                    "operator": "greater_than",
                    "expected": 1,
                    "effect": "deny",
                }
            ],
        },
        headers={"X-CSRF-Token": owner["csrf_token"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_policy_evaluation_is_deterministic_versioned_and_fail_closed(client):
    owner = sign_in(client, "policy-owner@example.dev", "Policy Owner")
    workspace = create_workspace(client, owner, "policy-control")
    policy = create_policy(client, owner, workspace["id"])
    headers = {"X-CSRF-Token": owner["csrf_token"]}
    path = f"/api/v1/workspaces/{workspace['id']}"

    evaluated = client.post(
        f"{path}/policy-evaluations/evaluate",
        json={
            "action": "infrastructure",
            "context": {
                "infrastructure": {
                    "tool": "opentofu",
                    "resource_destroys": 3,
                    "plan_has_changes": True,
                }
            },
        },
        headers=headers,
    )
    assert evaluated.status_code == 201, evaluated.text
    result = evaluated.json()
    assert result["decision"] == "deny"
    assert len(result["policy_snapshot_sha256"]) == 64
    assert result["results"][0]["rule_id"] == "deny-large-destroy"
    assert result["results"][0]["outcome"] == "matched"

    history = client.get(f"{path}/policy-evaluations?action=infrastructure")
    assert history.status_code == 200
    assert history.json()[0]["id"] == result["id"]

    updated = client.patch(
        f"{path}/policies/{policy['id']}",
        json={"expected_version": 1, "enabled": False},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    stale_update = client.patch(
        f"{path}/policies/{policy['id']}",
        json={"expected_version": 1, "enabled": True},
        headers=headers,
    )
    assert stale_update.status_code == 409

    after_disable = client.post(
        f"{path}/policy-evaluations/evaluate",
        json={"action": "infrastructure", "context": {"infrastructure": {"resource_destroys": 3}}},
        headers=headers,
    )
    assert after_disable.status_code == 201
    assert after_disable.json()["decision"] == "allow"
    assert after_disable.json()["policy_snapshot"] == []


def test_policy_exceptions_are_scoped_time_limited_and_independently_approved(client):
    owner = sign_in(client, "exception-owner@example.dev", "Exception Requester")
    workspace = create_workspace(client, owner, "exception-workspace")
    headers = {"X-CSRF-Token": owner["csrf_token"]}
    base = f"/api/v1/workspaces/{workspace['id']}"
    service_team = client.post(
        f"{base}/teams",
        json={"slug": "platform", "name": "Platform Engineering"},
        headers=headers,
    )
    assert service_team.status_code == 201, service_team.text
    service_response = client.post(
        f"{base}/services",
        json={
            "slug": "payments",
            "name": "Payments",
            "owner_team_id": service_team.json()["id"],
            "criticality": "tier_0",
        },
        headers=headers,
    )
    assert service_response.status_code == 201, service_response.text
    service = service_response.json()
    environment_response = client.post(
        f"{base}/environments",
        json={"slug": "staging", "name": "Staging", "environment_type": "staging"},
        headers=headers,
    )
    assert environment_response.status_code == 201, environment_response.text
    environment = environment_response.json()

    policy_response = client.post(
        f"{base}/policies",
        json={
            "slug": "critical-services",
            "name": "Critical service controls",
            "actions": ["deployment"],
            "enforcement_mode": "enforce",
            "rules": [
                {
                    "id": "tier-zero-requires-review",
                    "name": "Tier zero requires a review",
                    "field": "service.criticality",
                    "operator": "equals",
                    "expected": "tier_0",
                    "effect": "deny",
                }
            ],
        },
        headers=headers,
    )
    assert policy_response.status_code == 201, policy_response.text
    policy = policy_response.json()

    exception_response = client.post(
        f"{base}/policies/{policy['id']}/exceptions",
        json={
            "rule_id": "tier-zero-requires-review",
            "service_id": service["id"],
            "reason": "Approved migration window for the database cutover.",
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        },
        headers=headers,
    )
    assert exception_response.status_code == 201, exception_response.text
    exception = exception_response.json()
    self_approval = client.post(
        f"{base}/policy-exceptions/{exception['id']}/decision",
        json={"decision": "approve"},
        headers=headers,
    )
    assert self_approval.status_code == 403

    reviewer_client = TestClient(client.app)
    reviewer = sign_in(reviewer_client, "exception-reviewer@example.dev", "Policy Reviewer")
    membership = client.post(
        f"{base}/members",
        json={"email": reviewer["user"]["email"], "role": "platform_engineer"},
        headers=headers,
    )
    assert membership.status_code == 201, membership.text
    approved = reviewer_client.post(
        f"{base}/policy-exceptions/{exception['id']}/decision",
        json={"decision": "approve", "comment": "Time boxed exception reviewed."},
        headers={"X-CSRF-Token": reviewer["csrf_token"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    evaluation_body = {
        "action": "deployment",
        "service_id": service["id"],
        "environment_id": environment["id"],
    }
    excepted = client.post(
        f"{base}/policy-evaluations/evaluate", json=evaluation_body, headers=headers
    )
    assert excepted.status_code == 201, excepted.text
    assert excepted.json()["decision"] == "allow"
    assert excepted.json()["results"][0]["outcome"] == "excepted"

    revoked = reviewer_client.post(
        f"{base}/policy-exceptions/{exception['id']}/revoke",
        headers={"X-CSRF-Token": reviewer["csrf_token"]},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"
    denied = client.post(
        f"{base}/policy-evaluations/evaluate", json=evaluation_body, headers=headers
    )
    assert denied.status_code == 201, denied.text
    assert denied.json()["decision"] == "deny"


def test_policy_monitor_mode_reports_findings_without_blocking(client):
    owner = sign_in(client, "policy-monitor@example.dev", "Policy Monitor")
    workspace = create_workspace(client, owner, "policy-monitor")
    response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/policies",
        json={
            "slug": "monitor-large-destroy",
            "name": "Monitor large destructive plans",
            "actions": ["infrastructure"],
            "enforcement_mode": "monitor",
            "rules": [
                {
                    "id": "warn-on-destroy",
                    "name": "Warn on resource destruction",
                    "field": "infrastructure.resource_destroys",
                    "operator": "greater_than",
                    "expected": 0,
                    "effect": "deny",
                }
            ],
        },
        headers={"X-CSRF-Token": owner["csrf_token"]},
    )
    assert response.status_code == 201, response.text
    evaluated = client.post(
        f"/api/v1/workspaces/{workspace['id']}/policy-evaluations/evaluate",
        json={"action": "infrastructure", "context": {"infrastructure": {"resource_destroys": 1}}},
        headers={"X-CSRF-Token": owner["csrf_token"]},
    )
    assert evaluated.status_code == 201, evaluated.text
    assert evaluated.json()["decision"] == "allow"
    assert evaluated.json()["results"][0]["effect"] == "warn"
