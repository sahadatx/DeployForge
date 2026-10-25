from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.models import AuditEvent, Environment, UserSession, WorkspaceMembership


def sign_in(client: TestClient, email: str, display_name: str | None = None) -> dict:
    response = client.post(
        "/api/v1/auth/dev-login", json={"email": email, "display_name": display_name}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_auth_configuration_is_explicit_and_oidc_login_requires_provider(client):
    config = client.get("/api/v1/auth/config")
    assert config.status_code == 200
    assert config.json() == {
        "oidc_enabled": False,
        "development_login_enabled": True,
        "authentication_required": True,
    }

    response = client.get("/api/v1/auth/login")
    assert response.status_code == 503
    assert (
        response.json()["error"]["message"] == "An OIDC identity provider has not been configured"
    )


def test_development_login_issues_opaque_httponly_session_and_csrf_token(client):
    response = client.post(
        "/api/v1/auth/dev-login",
        json={"email": "first@example.dev", "display_name": "First Engineer"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["email"] == "first@example.dev"
    assert payload["user"]["display_name"] == "First Engineer"
    assert payload["user"]["is_platform_admin"] is True
    assert payload["csrf_token"]
    cookie_header = response.headers["set-cookie"]
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header

    raw_cookie = response.cookies.get(settings.session_cookie_name)
    assert raw_cookie and raw_cookie not in response.text
    with client.app.state.test_session_factory() as db:
        session = db.scalar(select(UserSession))
        assert session is not None
        assert session.token_hash != raw_cookie
        assert len(session.token_hash) == 64

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["id"] == payload["user"]["id"]


def test_development_auth_is_unavailable_outside_development(client, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    response = client.post("/api/v1/auth/dev-login", json={"email": "not-allowed@example.dev"})
    assert response.status_code == 404


def test_session_logout_requires_csrf_and_revokes_server_session(client):
    session = sign_in(client, "logout@example.dev")
    rejected = client.post("/api/v1/auth/logout")
    assert rejected.status_code == 403

    logged_out = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": session["csrf_token"]},
    )
    assert logged_out.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_workspace_access_is_membership_scoped_and_changes_are_audited(client):
    admin = sign_in(client, "admin@example.dev", "Workspace Admin")
    csrf = {"X-CSRF-Token": admin["csrf_token"]}

    assert client.get("/api/v1/workspaces").status_code == 200
    assert client.get("/api/v1/workspaces").json() == []
    assert (
        client.post(
            "/api/v1/workspaces", json={"name": "Production Platform", "slug": "production"}
        ).status_code
        == 403
    )

    created = client.post(
        "/api/v1/workspaces",
        json={
            "name": "Production Platform",
            "slug": "production",
            "description": "Production workload",
        },
        headers=csrf,
    )
    assert created.status_code == 201, created.text
    workspace = created.json()

    with client.app.state.test_session_factory() as db:
        db.add(
            Environment(
                id=uuid4(),
                workspace_id=UUID(workspace["id"]),
                slug="production",
                name="Production",
                environment_type="production",
            )
        )
        db.commit()
        admin_membership = db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == UUID(workspace["id"])
            )
        )
        assert admin_membership is not None and admin_membership.role == "workspace_admin"

    member_client = TestClient(client.app)
    member = sign_in(member_client, "auditor@example.dev", "Audit Reader")
    assert member["user"]["is_platform_admin"] is False
    assert member_client.get("/api/v1/workspaces").json() == []
    denied = member_client.get(f"/api/v1/workspaces/{workspace['id']}/environments")
    assert denied.status_code == 404

    granted = client.post(
        f"/api/v1/workspaces/{workspace['id']}/members",
        json={"email": "auditor@example.dev", "role": "auditor"},
        headers=csrf,
    )
    assert granted.status_code == 201, granted.text
    assert granted.json()["role"] == "auditor"

    visible_workspaces = member_client.get("/api/v1/workspaces")
    assert visible_workspaces.status_code == 200
    assert visible_workspaces.json()[0]["id"] == workspace["id"]
    assert visible_workspaces.json()[0]["role"] == "auditor"
    environments = member_client.get(f"/api/v1/workspaces/{workspace['id']}/environments")
    assert environments.status_code == 200
    assert environments.json()[0]["name"] == "Production"

    member_csrf = {"X-CSRF-Token": member["csrf_token"]}
    forbidden_admin_action = member_client.post(
        "/api/v1/workspaces",
        json={"name": "Forbidden Workspace", "slug": "forbidden"},
        headers=member_csrf,
    )
    assert forbidden_admin_action.status_code == 403

    audit = client.get("/api/v1/audit-logs")
    assert audit.status_code == 200
    actions = {event["action"] for event in audit.json()}
    assert "workspace.created" in actions
    assert "workspace.membership.granted" in actions
    assert "authorization.workspace_access.denied" in actions
    assert "authorization.platform_workspace_create.denied" in actions
    assert all(
        "actor_email" in event["details"] for event in audit.json() if event["actor_id"] is not None
    )

    member_audit = member_client.get(f"/api/v1/audit-logs?workspace_id={workspace['id']}")
    assert member_audit.status_code == 200
    assert all(event["workspace_id"] == workspace["id"] for event in member_audit.json())

    with client.app.state.test_session_factory() as db:
        assert (
            db.scalar(select(AuditEvent).where(AuditEvent.action == "workspace.membership.granted"))
            is not None
        )


def test_workspace_membership_mutations_enforce_roles_and_last_admin_guard(client):
    admin = sign_in(client, "root@example.dev")
    headers = {"X-CSRF-Token": admin["csrf_token"]}
    workspace_response = client.post(
        "/api/v1/workspaces",
        json={"name": "Platform", "slug": "platform"},
        headers=headers,
    )
    workspace_id = workspace_response.json()["id"]

    last_admin = client.delete(
        f"/api/v1/workspaces/{workspace_id}/members/{admin['user']['id']}",
        headers=headers,
    )
    assert last_admin.status_code == 409

    second_client = TestClient(client.app)
    second = sign_in(second_client, "engineer@example.dev")
    forbidden_grant = second_client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={"email": "root@example.dev", "role": "auditor"},
        headers={"X-CSRF-Token": second["csrf_token"]},
    )
    assert forbidden_grant.status_code == 404


def test_workspace_selector_and_environment_api_require_authentication(client):
    assert client.get("/api/v1/workspaces").status_code == 401
    assert client.get(f"/api/v1/workspaces/{uuid4()}/environments").status_code == 401
    assert client.get("/api/v1/audit-logs").status_code == 401
