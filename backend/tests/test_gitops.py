import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.builds import process_next_build_run
from app.core.config import settings
from app.core.deployments import process_next_deployment
from app.core.gitops import process_next_gitops_run
from app.integrations.argocd import ArgoCDClient, ArgoCDConfiguration
from app.models import GitOpsRun


def sign_in(client: TestClient, email: str, name: str) -> dict:
    response = client.post(
        "/api/v1/auth/dev-login",
        json={"email": email, "display_name": name},
    )
    assert response.status_code == 200, response.text
    return response.json()


def setup_workspace(client: TestClient, *, suffix: str = "one") -> dict:
    requester = sign_in(client, f"gitops-requester-{suffix}@example.dev", "GitOps Requester")
    headers = {"X-CSRF-Token": requester["csrf_token"]}
    workspace_response = client.post(
        "/api/v1/workspaces",
        json={"name": f"GitOps Workspace {suffix}", "slug": f"gitops-workspace-{suffix}"},
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
            "slug": "payments-api",
            "name": "Payments API",
            "owner_team_id": team_response.json()["id"],
            "repository_url": "https://github.com/acme/payments-api",
            "lifecycle": "production",
        },
        headers=headers,
    )
    assert service_response.status_code == 201, service_response.text
    environment_response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/environments",
        json={"slug": "staging", "name": "Staging", "environment_type": "staging"},
        headers=headers,
    )
    assert environment_response.status_code == 201, environment_response.text
    workspace.update(
        requester=requester,
        headers=headers,
        service=service_response.json(),
        environment=environment_response.json(),
    )
    target_response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/gitops-targets",
        json={
            "service_id": workspace["service"]["id"],
            "environment_id": workspace["environment"]["id"],
            "application_name": f"payments-api-staging-{suffix}",
            "expected_project": "platform",
            "expected_cluster_name": "staging-cluster",
        },
        headers=headers,
    )
    assert target_response.status_code == 201, target_response.text
    workspace["target"] = target_response.json()
    return workspace


def prepare_build_provider(monkeypatch) -> str:
    secret = "gitops-build-callback-secret-long-enough-12345"
    monkeypatch.setattr(settings, "github_actions_token", SecretStr("github-test-token"))
    monkeypatch.setattr(settings, "build_callback_secret", SecretStr(secret))
    monkeypatch.setattr(settings, "build_callback_public_url", "https://deployforge.example.test")
    monkeypatch.setattr(settings, "argocd_api_url", "https://argocd.example.test")
    monkeypatch.setattr(settings, "argocd_auth_token", SecretStr("argocd-test-token"))
    monkeypatch.setattr("app.core.builds.dispatch_workflow", lambda *args, **kwargs: None)
    return secret


def create_build_run(
    client: TestClient,
    workspace: dict,
    callback_secret: str,
    *,
    release_number: int,
    commit_sha: str,
    gitops_revision: str | None = None,
) -> tuple[str, str]:
    deployment_response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/deployments",
        json={
            "service_id": workspace["service"]["id"],
            "environment_id": workspace["environment"]["id"],
            "version": f"release-{release_number}",
            "source_ref": "main",
        },
        headers=workspace["headers"],
    )
    assert deployment_response.status_code == 202, deployment_response.text
    deployment = deployment_response.json()
    with client.app.state.test_session_factory() as db:
        assert process_next_deployment(db, "deployment-worker-test") is True

    approver_client = TestClient(client.app)
    approver = sign_in(
        approver_client,
        f"gitops-approver-{workspace['slug']}-{release_number}@example.dev",
        "GitOps Approver",
    )
    membership = client.post(
        f"/api/v1/workspaces/{workspace['id']}/members",
        json={"email": approver["user"]["email"], "role": "platform_engineer"},
        headers=workspace["headers"],
    )
    assert membership.status_code == 201, membership.text
    approval = approver_client.post(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{deployment['id']}/approval",
        json={"decision": "approve"},
        headers={"X-CSRF-Token": approver["csrf_token"]},
    )
    assert approval.status_code == 200, approval.text

    build_runs = client.get(
        f"/api/v1/workspaces/{workspace['id']}/build-runs?deployment_id={deployment['id']}"
    )
    assert build_runs.status_code == 200
    build_run_id = build_runs.json()[0]["id"]
    with client.app.state.test_session_factory() as db:
        assert process_next_build_run(db, "build-worker-test") is True

    artifact_digest = f"sha256:{release_number:064x}"
    sbom_digest = f"sha256:{release_number + 10:064x}"
    provenance_digest = f"sha256:{release_number + 20:064x}"
    body = json.dumps(
        {
            "delivery_id": f"gitops-{workspace['slug']}-{release_number}",
            "repository_url": "https://github.com/acme/payments-api",
            "external_run_id": f"{release_number}987654321",
            "run_url": f"https://github.com/acme/payments-api/actions/runs/{release_number}987654321",
            "commit_sha": commit_sha,
            "gitops_revision": gitops_revision,
            "status": "succeeded",
            "artifacts": [
                {
                    "name": "payments-api",
                    "artifact_type": "container",
                    "uri": f"oci://ghcr.io/acme/payments-api@{artifact_digest}",
                    "digest": artifact_digest,
                    "sbom_format": "cyclonedx-json",
                    "sbom_uri": "https://artifacts.example.test/payments/sbom.json",
                    "sbom_digest": sbom_digest,
                    "vulnerability_summary": {
                        "critical": 0,
                        "high": 0,
                        "medium": 0,
                        "low": 0,
                        "unknown": 0,
                    },
                    "signature_uri": "https://artifacts.example.test/payments/signature.bundle",
                    "signer_identity": "https://github.com/acme/payments-api/.github/workflows/build.yml@refs/heads/main",
                    "oidc_issuer": "https://token.actions.githubusercontent.com",
                    "provenance_uri": "https://artifacts.example.test/payments/provenance.jsonl",
                    "provenance_digest": provenance_digest,
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(
        callback_secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    result = client.post(
        f"/api/v1/build-runs/{build_run_id}/callback",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-DeployForge-Timestamp": timestamp,
            "X-DeployForge-Signature": f"sha256={signature}",
        },
    )
    assert result.status_code == 200, result.text
    return deployment["id"], build_run_id


def set_run_due(client: TestClient, gitops_run_id: str) -> None:
    with client.app.state.test_session_factory() as db:
        run = db.get(GitOpsRun, UUID(gitops_run_id))
        assert run is not None
        run.next_poll_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()


class FakeArgoCD:
    def __init__(self, *, fail_release_number: int | None = None) -> None:
        self.fail_release_number = fail_release_number
        self.calls: list[str] = []
        self.application = {
            "spec": {
                "project": "platform",
                "source": {"repoURL": "https://github.com/acme/payments-api"},
                "destination": {
                    "name": "staging-cluster",
                    "server": "https://kubernetes.example.test",
                },
            },
            "status": {
                "sync": {"status": "Synced", "revision": "old-revision"},
                "health": {"status": "Healthy"},
                "operationState": {"phase": "Succeeded"},
            },
        }

    def get_application(self, application_name: str) -> dict:
        assert application_name.startswith("payments-api-staging-")
        return self.application

    def sync_application(self, application_name: str, revision: str) -> dict:
        assert application_name.startswith("payments-api-staging-")
        self.calls.append(revision)
        if self.fail_release_number == len(self.calls):
            self.application["status"] = {
                "sync": {"status": "OutOfSync", "revision": self.calls[0]},
                "health": {"status": "Degraded"},
                "operationState": {"phase": "Failed"},
            }
        else:
            self.application["status"] = {
                "sync": {"status": "Synced", "revision": revision},
                "health": {"status": "Healthy"},
                "operationState": {"phase": "Succeeded"},
            }
        return {"metadata": {"name": application_name}}


def test_argocd_client_uses_bearer_token_and_requests_non_pruning_sync():
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"metadata": {"name": "payments-api-staging"}})
        return httpx.Response(200, json={"metadata": {"name": "payments-api-staging"}})

    client = ArgoCDClient(
        ArgoCDConfiguration(
            api_url="https://argocd.example.test", token="secret-test-token", timeout_seconds=4
        ),
        transport=httpx.MockTransport(respond),
    )
    client.get_application("payments-api-staging")
    client.sync_application("payments-api-staging", "a" * 40)
    assert requests[0].url.path == "/api/v1/applications/payments-api-staging"
    assert requests[0].headers["Authorization"] == "Bearer secret-test-token"
    assert requests[1].url.path == "/api/v1/applications/payments-api-staging/sync"
    assert json.loads(requests[1].content) == {
        "revision": "a" * 40,
        "prune": False,
        "dryRun": False,
    }


def test_gitops_target_status_is_explicitly_disconnected_without_credentials(client):
    workspace = setup_workspace(client)
    response = client.get(
        f"/api/v1/workspaces/{workspace['id']}/gitops-targets/{workspace['target']['id']}/status"
    )
    assert response.status_code == 200, response.text
    assert response.json()["provider_status"] == "disconnected"


def test_healthy_gitops_reconciliation_completes_deployment_on_exact_revision(client, monkeypatch):
    callback_secret = prepare_build_provider(monkeypatch)
    workspace = setup_workspace(client)
    argo = FakeArgoCD()
    monkeypatch.setattr("app.core.gitops._argo_client", lambda: argo)
    deployment_id, _ = create_build_run(
        client,
        workspace,
        callback_secret,
        release_number=1,
        commit_sha="a" * 40,
        gitops_revision="c" * 40,
    )
    build_response = client.get(
        f"/api/v1/workspaces/{workspace['id']}/build-runs?deployment_id={deployment_id}"
    )
    assert build_response.status_code == 200, build_response.text
    assert build_response.json()[0]["commit_sha"] == "a" * 40
    assert build_response.json()[0]["gitops_revision"] == "c" * 40
    runs_response = client.get(
        f"/api/v1/workspaces/{workspace['id']}/gitops-runs?deployment_id={deployment_id}"
    )
    assert runs_response.status_code == 200, runs_response.text
    gitops_run = runs_response.json()[0]
    assert gitops_run["status"] == "queued"
    assert gitops_run["desired_revision"] == "c" * 40
    assert gitops_run["artifact_digest"].startswith("sha256:")

    with client.app.state.test_session_factory() as db:
        assert process_next_gitops_run(db, "gitops-worker-test") is True
    syncing = client.get(
        f"/api/v1/workspaces/{workspace['id']}/gitops-runs/{gitops_run['id']}"
    ).json()
    assert syncing["status"] == "syncing"
    assert argo.calls == ["c" * 40]
    set_run_due(client, gitops_run["id"])
    with client.app.state.test_session_factory() as db:
        assert process_next_gitops_run(db, "gitops-worker-test") is True
    finished = client.get(
        f"/api/v1/workspaces/{workspace['id']}/gitops-runs/{gitops_run['id']}"
    ).json()
    assert finished["status"] == "succeeded"
    assert finished["sync_status"] == "Synced"
    assert finished["health_status"] == "Healthy"
    deployment = client.get(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{deployment_id}"
    ).json()
    assert deployment["status"] == "succeeded"


def test_failed_rollout_automatically_restores_previous_healthy_revision(client, monkeypatch):
    callback_secret = prepare_build_provider(monkeypatch)
    workspace = setup_workspace(client)
    argo = FakeArgoCD(fail_release_number=2)
    monkeypatch.setattr("app.core.gitops._argo_client", lambda: argo)

    first_deployment_id, _ = create_build_run(
        client, workspace, callback_secret, release_number=1, commit_sha="a" * 40
    )
    first_run = client.get(
        f"/api/v1/workspaces/{workspace['id']}/gitops-runs?deployment_id={first_deployment_id}"
    ).json()[0]
    with client.app.state.test_session_factory() as db:
        assert process_next_gitops_run(db, "gitops-worker-test") is True
    set_run_due(client, first_run["id"])
    with client.app.state.test_session_factory() as db:
        assert process_next_gitops_run(db, "gitops-worker-test") is True
    assert (
        client.get(
            f"/api/v1/workspaces/{workspace['id']}/deployments/{first_deployment_id}"
        ).json()["status"]
        == "succeeded"
    )

    second_deployment_id, _ = create_build_run(
        client, workspace, callback_secret, release_number=2, commit_sha="b" * 40
    )
    second_run = client.get(
        f"/api/v1/workspaces/{workspace['id']}/gitops-runs?deployment_id={second_deployment_id}"
    ).json()[0]
    with client.app.state.test_session_factory() as db:
        assert process_next_gitops_run(db, "gitops-worker-test") is True
    set_run_due(client, second_run["id"])
    with client.app.state.test_session_factory() as db:
        assert process_next_gitops_run(db, "gitops-worker-test") is True
    failed_run = client.get(
        f"/api/v1/workspaces/{workspace['id']}/gitops-runs/{second_run['id']}"
    ).json()
    assert failed_run["status"] == "failed"
    deployment_state = client.get(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{second_deployment_id}"
    ).json()
    assert deployment_state["status"] == "rollback_pending"

    rollback_run = client.get(
        f"/api/v1/workspaces/{workspace['id']}/gitops-runs?deployment_id={second_deployment_id}"
    ).json()[0]
    assert rollback_run["action_kind"] == "rollback"
    assert rollback_run["desired_revision"] == "a" * 40
    with client.app.state.test_session_factory() as db:
        assert process_next_gitops_run(db, "gitops-worker-test") is True
    set_run_due(client, rollback_run["id"])
    with client.app.state.test_session_factory() as db:
        assert process_next_gitops_run(db, "gitops-worker-test") is True
    final_state = client.get(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{second_deployment_id}"
    ).json()
    assert final_state["status"] == "rolled_back"
    assert argo.calls == ["a" * 40, "b" * 40, "a" * 40]


def test_operator_rollback_is_audited_and_only_applies_to_latest_target_release(
    client, monkeypatch
):
    callback_secret = prepare_build_provider(monkeypatch)
    workspace = setup_workspace(client)
    argo = FakeArgoCD()
    monkeypatch.setattr("app.core.gitops._argo_client", lambda: argo)

    deployment_ids: list[str] = []
    for release_number, revision in ((1, "a" * 40), (2, "b" * 40)):
        deployment_id, _ = create_build_run(
            client,
            workspace,
            callback_secret,
            release_number=release_number,
            commit_sha=revision,
        )
        deployment_ids.append(deployment_id)
        run = client.get(
            f"/api/v1/workspaces/{workspace['id']}/gitops-runs?deployment_id={deployment_id}"
        ).json()[0]
        with client.app.state.test_session_factory() as db:
            assert process_next_gitops_run(db, "gitops-worker-test") is True
        set_run_due(client, run["id"])
        with client.app.state.test_session_factory() as db:
            assert process_next_gitops_run(db, "gitops-worker-test") is True

    stale_rollback = client.post(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{deployment_ids[0]}/rollback",
        headers=workspace["headers"],
    )
    assert stale_rollback.status_code == 409

    rollback = client.post(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{deployment_ids[1]}/rollback",
        headers=workspace["headers"],
    )
    assert rollback.status_code == 202, rollback.text
    rollback_run = rollback.json()
    assert rollback_run["action_kind"] == "rollback"
    assert rollback_run["desired_revision"] == "a" * 40
    with client.app.state.test_session_factory() as db:
        assert process_next_gitops_run(db, "gitops-worker-test") is True
    set_run_due(client, rollback_run["id"])
    with client.app.state.test_session_factory() as db:
        assert process_next_gitops_run(db, "gitops-worker-test") is True

    final_deployment = client.get(
        f"/api/v1/workspaces/{workspace['id']}/deployments/{deployment_ids[1]}"
    )
    assert final_deployment.json()["status"] == "rolled_back"
    detail = client.get(f"/api/v1/workspaces/{workspace['id']}/gitops-runs/{rollback_run['id']}")
    assert any(event["event_type"] == "rollback.healthy" for event in detail.json()["events"])
    assert argo.calls == ["a" * 40, "b" * 40, "a" * 40]
