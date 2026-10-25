import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.core.builds import _recover_build_runs, process_next_build_run
from app.core.config import Settings, settings
from app.core.deployments import process_next_deployment
from app.integrations.github_actions import (
    DispatchConfiguration,
    GitHubDispatchError,
    dispatch_workflow,
    parse_github_repository,
)
from app.models import BuildRun


def sign_in(client: TestClient, email: str, name: str) -> dict:
    response = client.post(
        "/api/v1/auth/dev-login",
        json={"email": email, "display_name": name},
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_approved_release(
    client: TestClient, monkeypatch
) -> tuple[dict, dict, dict, dict, TestClient]:
    requester = sign_in(client, "build-requester@example.dev", "Build Requester")
    headers = {"X-CSRF-Token": requester["csrf_token"]}
    workspace_response = client.post(
        "/api/v1/workspaces",
        json={"name": "Build Platform", "slug": "build-platform"},
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
            "repository_url": "https://github.com/acme/checkout-api",
            "lifecycle": "production",
        },
        headers=headers,
    )
    assert service_response.status_code == 201, service_response.text
    service = service_response.json()
    environment_response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/environments",
        json={"slug": "staging", "name": "Staging", "environment_type": "staging"},
        headers=headers,
    )
    assert environment_response.status_code == 201, environment_response.text
    environment = environment_response.json()
    deployment_response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/deployments",
        json={
            "service_id": service["id"],
            "environment_id": environment["id"],
            "version": "release-candidate-7",
            "source_ref": "main",
        },
        headers=headers,
    )
    assert deployment_response.status_code == 202, deployment_response.text
    deployment = deployment_response.json()
    with client.app.state.test_session_factory() as db:
        assert process_next_deployment(db, "deployment-worker-test") is True
    waiting = client.get(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{deployment['id']}"
    ).json()
    assert waiting["status"] == "awaiting_approval"

    approver_client = TestClient(client.app)
    approver = sign_in(approver_client, "build-approver@example.dev", "Build Approver")
    grant = client.post(
        f"/api/v1/workspaces/{workspace['id']}/members",
        json={"email": approver["user"]["email"], "role": "platform_engineer"},
        headers=headers,
    )
    assert grant.status_code == 201, grant.text
    approved = approver_client.post(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{deployment['id']}/approval",
        json={"decision": "approve"},
        headers={"X-CSRF-Token": approver["csrf_token"]},
    )
    assert approved.status_code == 200, approved.text
    runs = approver_client.get(
        f"/api/v1/workspaces/{workspace['id']}/build-runs?deployment_id={deployment['id']}"
    )
    assert runs.status_code == 200
    return workspace, service, deployment, runs.json()[0], approver_client


def artifact_payload(*, critical: int = 0, high: int = 0) -> dict:
    artifact_digest = "sha256:" + "a" * 64
    sbom_digest = "sha256:" + "b" * 64
    provenance_digest = "sha256:" + "c" * 64
    return {
        "name": "checkout-api",
        "artifact_type": "container",
        "uri": f"oci://ghcr.io/acme/checkout-api@{artifact_digest}",
        "digest": artifact_digest,
        "sbom_format": "cyclonedx-json",
        "sbom_uri": "https://artifacts.example.test/checkout-api/sbom.json",
        "sbom_digest": sbom_digest,
        "vulnerability_summary": {
            "critical": critical,
            "high": high,
            "medium": 0,
            "low": 0,
            "unknown": 0,
        },
        "signature_uri": "https://artifacts.example.test/checkout-api/signature.bundle",
        "signer_identity": "https://github.com/acme/checkout-api/.github/workflows/deployforge-build.yml@refs/heads/main",
        "oidc_issuer": "https://token.actions.githubusercontent.com",
        "provenance_uri": "https://artifacts.example.test/checkout-api/provenance.jsonl",
        "provenance_digest": provenance_digest,
    }


def callback_body(
    build_run_id: str,
    *,
    delivery_id: str = "delivery-1",
    status: str = "succeeded",
    artifacts: list[dict] | None = None,
    repository_url: str = "https://github.com/acme/checkout-api",
) -> bytes:
    return json.dumps(
        {
            "delivery_id": delivery_id,
            "repository_url": repository_url,
            "external_run_id": "987654321",
            "run_url": "https://github.com/acme/checkout-api/actions/runs/987654321",
            "commit_sha": "d" * 40,
            "status": status,
            "failure_message": "Tests failed" if status == "failed" else None,
            "artifacts": artifacts
            if artifacts is not None
            else ([artifact_payload()] if status == "succeeded" else []),
        },
        separators=(",", ":"),
    ).encode()


def signed_headers(body: bytes, secret: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-DeployForge-Timestamp": timestamp,
        "X-DeployForge-Signature": f"sha256={signature}",
    }


def test_github_repository_parser_and_workflow_dispatch_are_scoped(client):
    assert parse_github_repository("https://github.com/acme/checkout-api.git") == (
        "acme",
        "checkout-api",
    )
    with pytest.raises(GitHubDispatchError):
        parse_github_repository("https://attacker.example/acme/checkout-api")
    with pytest.raises(GitHubDispatchError):
        parse_github_repository("https://user:password@github.com/acme/checkout-api")
    with pytest.raises(GitHubDispatchError):
        parse_github_repository("https://github.com/acme//checkout-api")

    build_run = BuildRun(
        id=uuid4(),
        workspace_id=uuid4(),
        deployment_id=uuid4(),
        service_id=uuid4(),
        attempt_number=1,
        provider="github_actions",
        repository_url="https://github.com/acme/checkout-api",
        workflow_path=".github/workflows/deployforge-build.yml",
        source_ref="main",
    )
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    dispatch_workflow(
        build_run,
        DispatchConfiguration(
            token="test-token",
            api_url="https://api.github.com",
            callback_url="https://deployforge.example.test",
        ),
        workflow_path=build_run.workflow_path,
        transport=httpx.MockTransport(respond),
    )
    assert len(requests) == 1
    assert requests[0].url.path == (
        "/repos/acme/checkout-api/actions/workflows/deployforge-build.yml/dispatches"
    )
    assert requests[0].headers["Authorization"] == "Bearer test-token"
    body = json.loads(requests[0].content)
    assert body["ref"] == "main"
    assert body["inputs"]["build_run_id"] == str(build_run.id)
    assert body["inputs"]["callback_url"].endswith(f"/build-runs/{build_run.id}/callback")


def test_build_callback_public_url_requires_a_secure_public_base():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, build_callback_public_url="http://deployforge.example.test")

    settings_with_url = Settings(
        _env_file=None,
        build_callback_public_url="https://deployforge.example.test/",
    )
    assert settings_with_url.build_callback_public_url == "https://deployforge.example.test"


def test_approved_deployment_dispatches_and_signed_callback_records_artifacts(client, monkeypatch):
    secret = "test-build-callback-secret-value-long-enough-123"
    monkeypatch.setattr(settings, "github_actions_token", SecretStr("github-test-token"))
    monkeypatch.setattr(settings, "build_callback_secret", SecretStr(secret))
    monkeypatch.setattr(settings, "build_callback_public_url", "https://deployforge.example.test")
    dispatched: list[tuple[str, str]] = []

    def fake_dispatch(build_run, configuration, *, workflow_path, transport=None):
        dispatched.append((build_run.source_ref, workflow_path))

    monkeypatch.setattr("app.core.builds.dispatch_workflow", fake_dispatch)
    workspace, _, deployment, run, callback_client = create_approved_release(client, monkeypatch)
    assert run["status"] == "queued"
    with client.app.state.test_session_factory() as db:
        assert process_next_build_run(db, "build-worker-test") is True
    build_run_response = callback_client.get(
        f"/api/v1/workspaces/{workspace['id']}/build-runs/{run['id']}"
    )
    assert build_run_response.status_code == 200, build_run_response.text
    assert build_run_response.json()["status"] == "dispatched"
    assert dispatched == [("main", ".github/workflows/deployforge-build.yml")]
    deployment_response = callback_client.get(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{deployment['id']}"
    )
    assert deployment_response.json()["status"] == "building"

    body = callback_body(run["id"])
    result = callback_client.post(
        f"/api/v1/build-runs/{run['id']}/callback",
        content=body,
        headers=signed_headers(body, secret),
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "succeeded"
    artifact = callback_client.get(
        f"/api/v1/workspaces/{workspace['id']}/build-runs/{run['id']}"
    ).json()["artifacts"][0]
    assert artifact["digest"] == "sha256:" + "a" * 64
    assert artifact["evidence_status"] == "workflow_reported"
    assert artifact["vulnerability_summary"]["critical"] == 0
    ready = callback_client.get(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{deployment['id']}"
    )
    assert ready.json()["status"] == "ready_to_deploy"

    replay = callback_client.post(
        f"/api/v1/build-runs/{run['id']}/callback",
        content=body,
        headers=signed_headers(body, secret),
    )
    assert replay.status_code == 200
    artifacts = callback_client.get(
        f"/api/v1/workspaces/{workspace['id']}/build-runs/{run['id']}"
    ).json()["artifacts"]
    assert len(artifacts) == 1


def test_artifact_policy_requires_a_second_independent_release_approval(client, monkeypatch):
    secret = "artifact-policy-test-secret-long-enough-123456"
    monkeypatch.setattr(settings, "github_actions_token", SecretStr("github-test-token"))
    monkeypatch.setattr(settings, "build_callback_secret", SecretStr(secret))
    monkeypatch.setattr(settings, "build_callback_public_url", "https://deployforge.example.test")
    monkeypatch.setattr("app.core.builds.dispatch_workflow", lambda *args, **kwargs: None)
    workspace, _, deployment, run, callback_client = create_approved_release(client, monkeypatch)
    requester = sign_in(client, "build-requester@example.dev", "Build Requester")
    policy = client.post(
        f"/api/v1/workspaces/{workspace['id']}/policies",
        json={
            "slug": "signed-artifact-release",
            "name": "Signed artifact release",
            "actions": ["deployment"],
            "enforcement_mode": "enforce",
            "rules": [
                {
                    "id": "signed-artifact-needs-review",
                    "name": "Signed artifacts need release review",
                    "field": "artifact.all_signed",
                    "operator": "equals",
                    "expected": True,
                    "effect": "require_approval",
                }
            ],
        },
        headers={"X-CSRF-Token": requester["csrf_token"]},
    )
    assert policy.status_code == 201, policy.text
    with client.app.state.test_session_factory() as db:
        assert process_next_build_run(db, "build-worker-test") is True
    body = callback_body(run["id"])
    callback = callback_client.post(
        f"/api/v1/build-runs/{run['id']}/callback",
        content=body,
        headers=signed_headers(body, secret),
    )
    assert callback.status_code == 200, callback.text
    assert callback.json()["status"] == "succeeded"
    waiting = callback_client.get(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{deployment['id']}"
    )
    assert waiting.json()["status"] == "awaiting_release_approval"

    approver = sign_in(callback_client, "build-approver@example.dev", "Build Approver")
    decision = callback_client.post(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{deployment['id']}/approval",
        json={"decision": "approve", "comment": "Artifact policy reviewed"},
        headers={"X-CSRF-Token": approver["csrf_token"]},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "ready_to_deploy"


def test_build_callback_rejects_bad_signature_and_wrong_repository(client, monkeypatch):
    secret = "another-build-callback-secret-long-enough-1234"
    monkeypatch.setattr(settings, "build_callback_secret", SecretStr(secret))
    _, _, _, run, callback_client = create_approved_release(client, monkeypatch)
    body = callback_body(run["id"], status="running")
    invalid_signature = callback_client.post(
        f"/api/v1/build-runs/{run['id']}/callback",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-DeployForge-Timestamp": str(int(time.time())),
            "X-DeployForge-Signature": f"sha256={'0' * 64}",
        },
    )
    assert invalid_signature.status_code == 401

    wrong_repo_body = callback_body(
        run["id"], status="running", repository_url="https://github.com/attacker/other-repo"
    )
    wrong_repo = callback_client.post(
        f"/api/v1/build-runs/{run['id']}/callback",
        content=wrong_repo_body,
        headers=signed_headers(wrong_repo_body, secret),
    )
    assert wrong_repo.status_code == 409


def test_build_callback_rejects_oversized_body_before_parsing(client, monkeypatch):
    secret = "large-callback-test-secret-long-enough-123"
    monkeypatch.setattr(settings, "build_callback_secret", SecretStr(secret))
    _, _, _, run, callback_client = create_approved_release(client, monkeypatch)
    response = callback_client.post(
        f"/api/v1/build-runs/{run['id']}/callback",
        content=b"{" + b" " * (256 * 1024),
        headers={
            "Content-Type": "application/json",
            "X-DeployForge-Timestamp": str(int(time.time())),
            "X-DeployForge-Signature": "sha256=invalid",
        },
    )
    assert response.status_code == 413


def test_missing_provider_blocks_build_without_claiming_deployment_success(client):
    workspace, _, deployment, run, callback_client = create_approved_release(client, None)
    with client.app.state.test_session_factory() as db:
        assert process_next_build_run(db, "build-worker-test") is True
    blocked = callback_client.get(
        f"/api/v1/workspaces/{workspace['id']}/build-runs/{run['id']}"
    ).json()
    assert blocked["status"] == "blocked"
    assert blocked["failure_code"] == "github_actions_not_configured"
    approved = callback_client.get(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{deployment['id']}"
    ).json()
    assert approved["status"] == "approved"


def test_reported_critical_vulnerability_blocks_release(client, monkeypatch):
    secret = "vulnerability-test-callback-secret-long-enough"
    monkeypatch.setattr(settings, "github_actions_token", SecretStr("github-test-token"))
    monkeypatch.setattr(settings, "build_callback_secret", SecretStr(secret))
    monkeypatch.setattr(settings, "build_callback_public_url", "https://deployforge.example.test")
    monkeypatch.setattr("app.core.builds.dispatch_workflow", lambda *args, **kwargs: None)
    workspace, _, deployment, run, callback_client = create_approved_release(client, monkeypatch)
    with client.app.state.test_session_factory() as db:
        assert process_next_build_run(db, "build-worker-test") is True
    body = callback_body(run["id"], artifacts=[artifact_payload(critical=1)])
    response = callback_client.post(
        f"/api/v1/build-runs/{run['id']}/callback",
        content=body,
        headers=signed_headers(body, secret),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"
    deployment_state = callback_client.get(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{deployment['id']}"
    ).json()
    assert deployment_state["status"] == "failed"
    assert deployment_state["failure_code"] == "vulnerability_threshold_exceeded"


def test_callback_timeout_preserves_the_actual_build_run_state(client, monkeypatch):
    secret = "timeout-test-callback-secret-long-enough-123"
    monkeypatch.setattr(settings, "github_actions_token", SecretStr("github-test-token"))
    monkeypatch.setattr(settings, "build_callback_secret", SecretStr(secret))
    monkeypatch.setattr(settings, "build_callback_public_url", "https://deployforge.example.test")
    monkeypatch.setattr("app.core.builds.dispatch_workflow", lambda *args, **kwargs: None)
    workspace, _, deployment, run, callback_client = create_approved_release(client, monkeypatch)
    with client.app.state.test_session_factory() as db:
        assert process_next_build_run(db, "build-worker-test") is True

    body = callback_body(run["id"], delivery_id="timeout-running", status="running")
    response = callback_client.post(
        f"/api/v1/build-runs/{run['id']}/callback",
        content=body,
        headers=signed_headers(body, secret),
    )
    assert response.status_code == 200, response.text
    with client.app.state.test_session_factory() as db:
        build_run = db.get(BuildRun, UUID(run["id"]))
        assert build_run is not None
        build_run.callback_deadline_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        _recover_build_runs(db, now=datetime.now(UTC))

    detail = callback_client.get(
        f"/api/v1/workspaces/{workspace['id']}/build-runs/{run['id']}"
    ).json()
    timeout_event = next(
        event for event in detail["events"] if event["event_type"] == "callback.timed_out"
    )
    assert timeout_event["from_status"] == "running"
    assert timeout_event["to_status"] == "failed"
    deployment_state = callback_client.get(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{deployment['id']}"
    ).json()
    assert deployment_state["failure_code"] == "build_callback_timeout"
