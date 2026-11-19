import json
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import settings
from app.models import GitHubRepositoryProvisioning, Service
from tests.auth_helpers import sign_in


def create_workspace(client: TestClient, session: dict, slug: str = "platform") -> dict:
    response = client.post(
        "/api/v1/workspaces",
        json={"name": "Production Platform", "slug": slug},
        headers={"X-CSRF-Token": session["csrf_token"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_team(client: TestClient, workspace_id: str, csrf_token: str) -> dict:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/teams",
        json={"slug": "payments", "name": "Payments", "description": "Checkout and billing"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_service_catalog_lifecycle_golden_paths_and_audit(client):
    session = sign_in(client, "platform@example.dev", "Platform Engineer")
    csrf = {"X-CSRF-Token": session["csrf_token"]}
    workspace = create_workspace(client, session)
    workspace_id = workspace["id"]
    team = create_team(client, workspace_id, session["csrf_token"])

    teams = client.get(f"/api/v1/workspaces/{workspace_id}/teams")
    assert teams.status_code == 200
    assert teams.json()[0]["service_count"] == 0
    assert teams.json()[0]["name"] == "Payments"

    golden_paths = client.get(f"/api/v1/workspaces/{workspace_id}/golden-paths")
    assert golden_paths.status_code == 200
    assert {path["slug"] for path in golden_paths.json()} == {
        "api-service",
        "web-application",
        "background-worker",
    }

    created = client.post(
        f"/api/v1/workspaces/{workspace_id}/services",
        json={
            "slug": "checkout-api",
            "name": "Checkout API",
            "description": "Owns checkout sessions",
            "system": "Commerce",
            "owner_team_id": team["id"],
            "repository_url": "https://github.com/acme/checkout-api",
            "documentation_url": "https://docs.acme.dev/checkout",
            "lifecycle": "experimental",
            "criticality": "tier_1",
            "tags": ["payments", "customer-facing"],
            "metadata": {"runtime": "Python"},
        },
        headers=csrf,
    )
    assert created.status_code == 201, created.text
    service = created.json()
    assert service["owner_team_name"] == "Payments"
    assert service["criticality"] == "tier_1"
    assert service["metadata"] == {"runtime": "Python"}
    assert service["tags"] == ["payments", "customer-facing"]

    rejected_url = client.post(
        f"/api/v1/workspaces/{workspace_id}/services",
        json={
            "slug": "unsafe-url",
            "name": "Unsafe URL",
            "owner_team_id": team["id"],
            "repository_url": "javascript:alert(1)",
        },
        headers=csrf,
    )
    assert rejected_url.status_code == 422

    duplicate = client.post(
        f"/api/v1/workspaces/{workspace_id}/services",
        json={
            "slug": "checkout-api",
            "name": "Duplicate Checkout",
            "owner_team_id": team["id"],
        },
        headers=csrf,
    )
    assert duplicate.status_code == 409

    invalid_team = client.post(
        f"/api/v1/workspaces/{workspace_id}/services",
        json={
            "slug": "wrong-owner",
            "name": "Wrong Owner",
            "owner_team_id": "00000000-0000-0000-0000-000000000000",
        },
        headers=csrf,
    )
    assert invalid_team.status_code == 422

    listing = client.get(
        f"/api/v1/workspaces/{workspace_id}/services?search=payments&lifecycle=experimental"
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == service["id"]

    updated = client.patch(
        f"/api/v1/workspaces/{workspace_id}/services/{service['id']}",
        json={"lifecycle": "production", "criticality": "tier_0", "tags": ["payments"]},
        headers=csrf,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["lifecycle"] == "production"
    assert updated.json()["criticality"] == "tier_0"
    assert updated.json()["tags"] == ["payments"]

    instantiated = client.post(
        f"/api/v1/workspaces/{workspace_id}/golden-paths/api-service/instantiate",
        json={
            "slug": "ledger-api",
            "name": "Ledger API",
            "owner_team_id": team["id"],
            "metadata": {"runtime": "Go", "visibility": "public"},
        },
        headers=csrf,
    )
    assert instantiated.status_code == 201, instantiated.text
    assert instantiated.json()["golden_path_slug"] == "api-service"
    assert instantiated.json()["metadata"] == {"runtime": "Go", "visibility": "public"}

    invalid_path_config = client.post(
        f"/api/v1/workspaces/{workspace_id}/golden-paths/api-service/instantiate",
        json={
            "slug": "invalid-path-config",
            "name": "Invalid Path Config",
            "owner_team_id": team["id"],
            "metadata": {"runtime": "Go", "unknown": "not allowed"},
        },
        headers=csrf,
    )
    assert invalid_path_config.status_code == 422

    archived = client.post(
        f"/api/v1/workspaces/{workspace_id}/services/{service['id']}/archive",
        headers=csrf,
    )
    assert archived.status_code == 200
    assert archived.json()["lifecycle"] == "retired"
    assert (
        client.get(f"/api/v1/workspaces/{workspace_id}/services/{service['id']}").status_code == 200
    )

    summary = client.get(f"/api/v1/workspaces/{workspace_id}/services/summary")
    assert summary.status_code == 200
    assert summary.json() == {
        "total": 2,
        "production": 0,
        "experimental": 1,
        "ownership_teams": 1,
    }

    team_list = client.get(f"/api/v1/workspaces/{workspace_id}/teams").json()
    assert team_list[0]["service_count"] == 2
    audit = client.get(f"/api/v1/audit-logs?workspace_id={workspace_id}")
    assert audit.status_code == 200
    actions = {event["action"] for event in audit.json()}
    assert "catalog.team.created" in actions
    assert "catalog.service.created" in actions
    assert "catalog.service.updated" in actions
    assert "catalog.service.archived" in actions
    assert "catalog.golden_path.instantiated" in actions


def test_service_catalog_is_workspace_scoped_and_write_permissions_are_enforced(client):
    admin = sign_in(client, "catalog-admin@example.dev", "Catalog Admin")
    workspace = create_workspace(client, admin)
    workspace_id = workspace["id"]
    team = create_team(client, workspace_id, admin["csrf_token"])

    member_client = TestClient(client.app)
    member = sign_in(member_client, "catalog-auditor@example.dev", "Catalog Auditor")
    grant = client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={"email": "catalog-auditor@example.dev", "role": "auditor"},
        headers={"X-CSRF-Token": admin["csrf_token"]},
    )
    assert grant.status_code == 201

    catalog = member_client.get(f"/api/v1/workspaces/{workspace_id}/services")
    assert catalog.status_code == 200
    assert catalog.json()["items"] == []
    forbidden_team = member_client.post(
        f"/api/v1/workspaces/{workspace_id}/teams",
        json={"slug": "readonly-team", "name": "Read Only"},
        headers={"X-CSRF-Token": member["csrf_token"]},
    )
    assert forbidden_team.status_code == 403
    forbidden_service = member_client.post(
        f"/api/v1/workspaces/{workspace_id}/services",
        json={"slug": "blocked", "name": "Blocked Service", "owner_team_id": team["id"]},
        headers={"X-CSRF-Token": member["csrf_token"]},
    )
    assert forbidden_service.status_code == 403

    other_workspace = create_workspace(client, admin, slug="another-platform")
    hidden = member_client.get(f"/api/v1/workspaces/{other_workspace['id']}/services")
    assert hidden.status_code == 404
    anonymous = TestClient(client.app)
    assert anonymous.get(f"/api/v1/workspaces/{workspace_id}/services").status_code == 401


def test_golden_path_repository_bootstrap_is_queued_then_completed_by_worker(client, monkeypatch):
    import app.core.repository_provisioning as repository_worker

    monkeypatch.setattr(settings, "github_repository_owner", "acme-platform")
    monkeypatch.setattr(
        settings,
        "github_repository_templates",
        json.dumps({"api-service": "acme-platform/api-template"}),
    )
    monkeypatch.setattr(settings, "github_repository_private", True)
    monkeypatch.setattr(settings, "build_callback_secret", SecretStr("b" * 48))
    monkeypatch.setattr(
        settings, "build_callback_public_url", "https://deployforge.example.com"
    )
    session = sign_in(client, "repository-bootstrap@example.dev", "Repository Bootstrap")
    workspace = create_workspace(client, session, slug="repository-bootstrap")
    workspace_id = workspace["id"]
    team = create_team(client, workspace_id, session["csrf_token"])
    headers = {"X-CSRF-Token": session["csrf_token"]}

    paths = client.get(f"/api/v1/workspaces/{workspace_id}/golden-paths")
    assert paths.status_code == 200
    assert next(path for path in paths.json() if path["slug"] == "api-service")[
        "repository_bootstrap_available"
    ]

    created = client.post(
        f"/api/v1/workspaces/{workspace_id}/golden-paths/api-service/instantiate",
        json={
            "slug": "checkout-api",
            "name": "Checkout API",
            "owner_team_id": team["id"],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    service_id = created.json()["id"]

    queued = client.post(
        f"/api/v1/workspaces/{workspace_id}/services/{service_id}/repository-provisioning",
        headers=headers,
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["status"] == "queued"
    assert queued.json()["is_private"] is True

    duplicate = client.post(
        f"/api/v1/workspaces/{workspace_id}/services/{service_id}/repository-provisioning",
        headers=headers,
    )
    assert duplicate.status_code == 409

    class FakeGitHubRepositoryClient:
        def configure_actions_bootstrap(
            self,
            owner: str,
            name: str,
            *,
            callback_secret: str,
            callback_allowed_host: str,
        ):
            assert (owner, name) == ("acme-platform", "checkout-api")
            assert callback_secret == "b" * 48
            assert callback_allowed_host == "deployforge.example.com"

        def get_repository(self, owner: str, name: str):
            assert (owner, name) == ("acme-platform", "checkout-api")
            return None

        def generate_from_template(self, **values):
            assert values["template_owner"] == "acme-platform"
            assert values["template_name"] == "api-template"
            assert values["private"] is True
            return {
                "id": 41,
                "full_name": "acme-platform/checkout-api",
                "private": True,
                "default_branch": "main",
                "template_repository": {"full_name": "acme-platform/api-template"},
            }

    monkeypatch.setattr(
        repository_worker, "_repository_client", lambda: FakeGitHubRepositoryClient()
    )
    with client.app.state.test_session_factory() as db:
        assert repository_worker.process_next_repository_provisioning(db, "worker-test")
        stored_service = db.get(Service, UUID(service_id))
        stored_job = db.get(GitHubRepositoryProvisioning, UUID(queued.json()["id"]))
        assert stored_job.status == "succeeded"
        assert stored_job.external_repository_id == "41"
        assert stored_service.repository_url == "https://github.com/acme-platform/checkout-api"

    listed = client.get(f"/api/v1/workspaces/{workspace_id}/repository-provisionings")
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "succeeded"
