import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import settings
from app.core.infrastructure import process_next_infrastructure_run


def sign_in(client: TestClient, email: str, name: str) -> dict:
    response = client.post(
        "/api/v1/auth/dev-login",
        json={"email": email, "display_name": name},
    )
    assert response.status_code == 200, response.text
    return response.json()


def prepare_provider(monkeypatch) -> str:
    secret = "infrastructure-callback-secret-long-enough-12345"
    monkeypatch.setattr(settings, "github_actions_token", SecretStr("github-infra-test-token"))
    monkeypatch.setattr(settings, "build_callback_secret", SecretStr(secret))
    monkeypatch.setattr(settings, "build_callback_public_url", "https://deployforge.example.test")
    monkeypatch.setattr(settings, "build_callback_timeout_seconds", 600)
    monkeypatch.setattr(
        settings,
        "infrastructure_workflow_path",
        ".github/workflows/deployforge-infrastructure.yml",
    )
    return secret


def setup_environment(
    client: TestClient,
    *,
    suffix: str,
    environment_type: str = "staging",
    quota_cpu_millis: int | None = 2000,
) -> dict:
    owner = sign_in(client, f"infra-owner-{suffix}@example.dev", "Infrastructure Owner")
    headers = {"X-CSRF-Token": owner["csrf_token"]}
    workspace_response = client.post(
        "/api/v1/workspaces",
        json={"name": f"Infrastructure {suffix}", "slug": f"infra-{suffix}"},
        headers=headers,
    )
    assert workspace_response.status_code == 201, workspace_response.text
    workspace = workspace_response.json()
    environment_response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/environments",
        json={
            "slug": environment_type,
            "name": environment_type.title(),
            "environment_type": environment_type,
            "cluster_name": "platform-staging",
            "namespace": "deployforge-staging",
            "quota_cpu_millis": quota_cpu_millis,
            "quota_memory_mib": 4096,
            "quota_pods": 40,
            "quota_persistent_volume_claims": 8,
            "quota_services": 20,
        },
        headers=headers,
    )
    assert environment_response.status_code == 201, environment_response.text
    environment = environment_response.json()
    target_response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/infrastructure-targets",
        json={
            "environment_id": environment["id"],
            "repository_url": "https://github.com/acme/platform-infra",
            "source_ref": "main",
            "working_directory": "environments/staging",
            "tool": "opentofu",
            "backend_workspace": "deployforge-staging",
        },
        headers=headers,
    )
    assert target_response.status_code == 201, target_response.text
    return {
        "id": workspace["id"],
        "headers": headers,
        "owner": owner,
        "environment": environment,
        "target": target_response.json(),
    }


def signed_callback(
    client: TestClient,
    secret: str,
    run_id: str,
    payload: dict,
    *,
    delivery_id: str,
) -> object:
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return client.post(
        f"/api/v1/infrastructure-runs/{run_id}/callback",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-DeployForge-Timestamp": timestamp,
            "X-DeployForge-Signature": f"sha256={signature}",
        },
    )


def plan_payload(
    delivery_id: str,
    *,
    has_changes: bool = True,
    cpu_millis: int = 1500,
    destroys: int = 0,
) -> dict:
    return {
        "delivery_id": delivery_id,
        "operation": "plan",
        "status": "planned",
        "external_run_id": f"workflow-{delivery_id}",
        "run_url": f"https://github.com/acme/platform-infra/actions/runs/{delivery_id}",
        "plan_digest": f"sha256:{'a' * 64}",
        "plan_artifact_uri": f"https://github.com/acme/platform-infra/actions/runs/{delivery_id}/artifacts/1",
        "plan_has_changes": has_changes,
        "resource_additions": 2 if has_changes else 0,
        "resource_changes": 1 if has_changes else 0,
        "resource_destroys": destroys,
        "quota_usage": {
            "cpu_millis": cpu_millis,
            "memory_mib": 1024,
            "pods": 6,
            "persistent_volume_claims": 2,
            "services": 4,
        },
    }


def queue_run(client: TestClient, workspace: dict, run_kind: str = "plan") -> dict:
    response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/environments/"
        f"{workspace['environment']['id']}/infrastructure-runs",
        json={"run_kind": run_kind},
        headers=workspace["headers"],
    )
    assert response.status_code == 202, response.text
    return response.json()


def dispatch_run(client: TestClient, workspace: dict, run_id: str) -> None:
    with client.app.state.test_session_factory() as db:
        assert process_next_infrastructure_run(db, "infrastructure-worker-test") is True
    run = client.get(f"/api/v1/workspaces/{workspace['id']}/infrastructure-runs/{run_id}").json()
    assert run["status"] == "dispatched"


def test_plan_requires_independent_approval_and_applies_exact_saved_plan(client, monkeypatch):
    secret = prepare_provider(monkeypatch)
    workspace = setup_environment(client, suffix="approval")
    dispatches: list[dict] = []

    def capture_dispatch(repository_url, source_ref, configuration, **kwargs):
        dispatches.append(
            {
                "repository_url": repository_url,
                "source_ref": source_ref,
                "workflow_path": kwargs["workflow_path"],
                "inputs": kwargs["inputs"],
            }
        )

    monkeypatch.setattr("app.core.infrastructure.dispatch_workflow_inputs", capture_dispatch)
    run = queue_run(client, workspace)
    dispatch_run(client, workspace, run["id"])
    assert dispatches[0]["repository_url"] == "https://github.com/acme/platform-infra"
    assert dispatches[0]["workflow_path"] == ".github/workflows/deployforge-infrastructure.yml"
    assert dispatches[0]["inputs"]["tool"] == "opentofu"
    assert dispatches[0]["inputs"]["operation"] == "plan"
    assert dispatches[0]["inputs"]["namespace"] == "deployforge-staging"

    planned = signed_callback(
        client, secret, run["id"], plan_payload("plan-ready"), delivery_id="plan-ready"
    )
    assert planned.status_code == 200, planned.text
    assert planned.json()["status"] == "awaiting_approval"
    assert planned.json()["resource_destroys"] == 0

    duplicate = signed_callback(
        client, secret, run["id"], plan_payload("plan-ready"), delivery_id="plan-ready"
    )
    assert duplicate.status_code == 200, duplicate.text
    another_run = client.post(
        f"/api/v1/workspaces/{workspace['id']}/environments/"
        f"{workspace['environment']['id']}/infrastructure-runs",
        json={"run_kind": "plan"},
        headers=workspace["headers"],
    )
    assert another_run.status_code == 409

    self_approval = client.post(
        f"/api/v1/workspaces/{workspace['id']}/infrastructure-runs/{run['id']}/approve",
        json={"comment": "looks good"},
        headers=workspace["headers"],
    )
    assert self_approval.status_code == 403

    approver_client = TestClient(client.app)
    approver = sign_in(approver_client, "infra-approver@example.dev", "Infrastructure Approver")
    membership = client.post(
        f"/api/v1/workspaces/{workspace['id']}/members",
        json={"email": approver["user"]["email"], "role": "platform_engineer"},
        headers=workspace["headers"],
    )
    assert membership.status_code == 201, membership.text
    approved = approver_client.post(
        f"/api/v1/workspaces/{workspace['id']}/infrastructure-runs/{run['id']}/approve",
        json={"comment": "reviewed plan"},
        headers={"X-CSRF-Token": approver["csrf_token"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "apply_queued"

    dispatch_run(client, workspace, run["id"])
    assert dispatches[1]["inputs"]["operation"] == "apply"
    assert dispatches[1]["inputs"]["plan_digest"] == plan_payload("plan-ready")["plan_digest"]
    assert (
        dispatches[1]["inputs"]["plan_artifact_uri"]
        == plan_payload("plan-ready")["plan_artifact_uri"]
    )

    apply_started = signed_callback(
        client,
        secret,
        run["id"],
        {"delivery_id": "apply-running", "operation": "apply", "status": "running"},
        delivery_id="apply-running",
    )
    assert apply_started.status_code == 200, apply_started.text
    assert apply_started.json()["status"] == "applying"
    applied = signed_callback(
        client,
        secret,
        run["id"],
        {
            "delivery_id": "apply-done",
            "operation": "apply",
            "status": "succeeded",
            "plan_digest": plan_payload("plan-ready")["plan_digest"],
        },
        delivery_id="apply-done",
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "succeeded"
    details = client.get(
        f"/api/v1/workspaces/{workspace['id']}/infrastructure-runs/{run['id']}"
    ).json()
    assert [event["sequence"] for event in details["events"]] == sorted(
        event["sequence"] for event in details["events"]
    )
    assert any(event["event_type"] == "plan.approved" for event in details["events"])


def test_plan_is_blocked_when_reported_usage_exceeds_environment_quota(client, monkeypatch):
    secret = prepare_provider(monkeypatch)
    workspace = setup_environment(client, suffix="quota", quota_cpu_millis=1000)
    monkeypatch.setattr("app.core.infrastructure.dispatch_workflow_inputs", lambda *a, **k: None)
    run = queue_run(client, workspace)
    dispatch_run(client, workspace, run["id"])
    response = signed_callback(
        client,
        secret,
        run["id"],
        plan_payload("over-quota", cpu_millis=1250),
        delivery_id="over-quota",
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"
    assert response.json()["failure_code"] == "environment_quota_exceeded"

    second_run = queue_run(client, workspace)
    dispatch_run(client, workspace, second_run["id"])
    incomplete_payload = plan_payload("missing-quota-evidence")
    incomplete_payload["quota_usage"].pop("services")
    incomplete = signed_callback(
        client,
        secret,
        second_run["id"],
        incomplete_payload,
        delivery_id="missing-quota-evidence",
    )
    assert incomplete.status_code == 200, incomplete.text
    assert incomplete.json()["status"] == "blocked"
    assert incomplete.json()["failure_code"] == "environment_quota_exceeded"


def test_enforced_policy_blocks_infrastructure_plan_before_approval(client, monkeypatch):
    secret = prepare_provider(monkeypatch)
    workspace = setup_environment(client, suffix="policy-plan")
    monkeypatch.setattr("app.core.infrastructure.dispatch_workflow_inputs", lambda *a, **k: None)
    policy = client.post(
        f"/api/v1/workspaces/{workspace['id']}/policies",
        json={
            "slug": "limit-destructive-plans",
            "name": "Limit destructive plans",
            "actions": ["infrastructure"],
            "enforcement_mode": "enforce",
            "rules": [
                {
                    "id": "deny-many-destroys",
                    "name": "Deny large destroy plans",
                    "field": "infrastructure.resource_destroys",
                    "operator": "greater_than",
                    "expected": 1,
                    "effect": "deny",
                }
            ],
        },
        headers=workspace["headers"],
    )
    assert policy.status_code == 201, policy.text
    run = queue_run(client, workspace)
    dispatch_run(client, workspace, run["id"])
    blocked = signed_callback(
        client,
        secret,
        run["id"],
        plan_payload("policy-blocked", destroys=2),
        delivery_id="policy-blocked",
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["failure_code"] == "policy_denied"
    details = client.get(
        f"/api/v1/workspaces/{workspace['id']}/infrastructure-runs/{run['id']}"
    ).json()
    event = next(
        event for event in details["events"] if event["event_type"] == "plan.blocked_policy"
    )
    assert event["details"]["policy_evaluation_id"]


def test_protected_production_rejects_destructive_plan_and_drift_run_never_applies(
    client, monkeypatch
):
    secret = prepare_provider(monkeypatch)
    workspace = setup_environment(
        client, suffix="production", environment_type="production", quota_cpu_millis=None
    )
    assert workspace["environment"]["protected"] is True
    monkeypatch.setattr("app.core.infrastructure.dispatch_workflow_inputs", lambda *a, **k: None)

    destructive = queue_run(client, workspace)
    dispatch_run(client, workspace, destructive["id"])
    blocked = signed_callback(
        client,
        secret,
        destructive["id"],
        plan_payload("protected-delete", destroys=1),
        delivery_id="protected-delete",
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["failure_code"] == "protected_environment_destroy_denied"

    drift = queue_run(client, workspace, "drift")
    dispatch_run(client, workspace, drift["id"])
    drifted = signed_callback(
        client,
        secret,
        drift["id"],
        plan_payload("drift-result", has_changes=True),
        delivery_id="drift-result",
    )
    assert drifted.status_code == 200, drifted.text
    assert drifted.json()["status"] == "drifted"


def test_workspace_operator_cannot_remove_production_protection(client):
    workspace = setup_environment(
        client, suffix="production-access", environment_type="production", quota_cpu_millis=None
    )
    operator_client = TestClient(client.app)
    operator = sign_in(operator_client, "infra-operator@example.dev", "Infrastructure Operator")
    membership = client.post(
        f"/api/v1/workspaces/{workspace['id']}/members",
        json={"email": operator["user"]["email"], "role": "platform_engineer"},
        headers=workspace["headers"],
    )
    assert membership.status_code == 201, membership.text
    endpoint = f"/api/v1/workspaces/{workspace['id']}/environments/{workspace['environment']['id']}"
    unprotect = operator_client.patch(
        endpoint,
        json={"protected": False},
        headers={"X-CSRF-Token": operator["csrf_token"]},
    )
    assert unprotect.status_code == 403
    enable_destroy = operator_client.patch(
        endpoint,
        json={"allow_destroy": True},
        headers={"X-CSRF-Token": operator["csrf_token"]},
    )
    assert enable_destroy.status_code == 403
