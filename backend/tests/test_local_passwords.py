from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select, text

from app import local_accounts
from app.core.config import settings
from app.core.local_passwords import (
    LOCAL_ISSUER,
    LocalLoginLimiter,
    hash_password,
    local_login_limiter,
    verify_password,
)
from app.models import AuditEvent, User, UserSession
from tests.auth_helpers import TEST_PASSWORD, provision_local_user, sign_in

LOGIN = "/api/v1/auth/password-login"
EMAIL = "local-password@example.dev"


def test_password_hashes_are_salted_versioned_and_never_plaintext():
    first, second = hash_password(TEST_PASSWORD), hash_password(TEST_PASSWORD)
    assert first.startswith("scrypt$v1$")
    assert first != second
    assert TEST_PASSWORD not in first
    assert verify_password(TEST_PASSWORD, first)
    assert not verify_password("incorrect-password", first)
    assert not verify_password(TEST_PASSWORD, None)
    assert not verify_password(TEST_PASSWORD, "malformed-hash")
    assert not verify_password(TEST_PASSWORD, first.replace("$32768$", "$1073741824$"))


@pytest.mark.parametrize("password", ["short", " " * 12, "x" * 129])
def test_operator_password_limits(password):
    with pytest.raises(ValueError, match="12–128"):
        hash_password(password)


def test_password_login_is_case_insensitive_and_preserves_identity_roles(client):
    session = sign_in(client, EMAIL, "Local Operator")
    response = client.post(LOGIN, json={"email": EMAIL.upper(), "password": TEST_PASSWORD})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["user"]["id"] == session["user"]["id"]
    assert payload["user"]["is_platform_admin"] is True
    assert payload["user"]["email_verified"] is False
    assert "password" not in payload["user"]
    assert "password_hash" not in payload["user"]
    assert TEST_PASSWORD not in response.text
    assert "HttpOnly" in response.headers["set-cookie"]
    assert payload["csrf_token"]
    assert client.get("/api/v1/auth/me").json()["user"]["id"] == session["user"]["id"]
    with client.app.state.test_session_factory() as db:
        events = db.scalars(select(AuditEvent)).all()
        assert TEST_PASSWORD not in repr([event.details for event in events])


@pytest.mark.parametrize("kind", ["wrong", "unknown", "passwordless", "inactive", "oidc"])
def test_credentials_fail_generically_and_never_create_or_claim_user(client, kind):
    password = TEST_PASSWORD
    with client.app.state.test_session_factory() as db:
        if kind != "unknown":
            user = User(
                issuer="https://identity.example.dev" if kind == "oidc" else LOCAL_ISSUER,
                subject=EMAIL,
                email=EMAIL,
                display_name="Existing Identity",
                password_hash=None if kind == "passwordless" else hash_password(TEST_PASSWORD),
                active=kind != "inactive",
                email_verified=kind == "oidc",
            )
            db.add(user)
            db.commit()
        if kind == "wrong":
            password = "incorrect-password"
    response = client.post(LOGIN, json={"email": EMAIL, "password": password})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Email or password is incorrect"
    assert "set-cookie" not in response.headers
    with client.app.state.test_session_factory() as db:
        assert len(db.scalars(select(User)).all()) == (0 if kind == "unknown" else 1)
        assert not db.scalars(select(UserSession)).all()


@pytest.mark.parametrize(
    "payload",
    [
        {"email": EMAIL},
        {"email": EMAIL, "password": ""},
        {"email": EMAIL, "password": "validation-secret-" * 20},
        {"email": EMAIL, "password": TEST_PASSWORD, "display_name": "Anonymous Rename"},
    ],
)
def test_login_requires_bounded_secret_password_without_echoing_input(client, payload):
    response = client.post(LOGIN, json=payload)
    assert response.status_code == 422
    assert TEST_PASSWORD not in response.text
    assert "validation-secret" not in response.text
    assert "Anonymous Rename" not in response.text


@pytest.mark.parametrize("path", [LOGIN, "/api/v1/auth/dev-login"])
def test_both_login_routes_require_password_and_validate_origin(client, path, monkeypatch):
    provision_local_user(client, EMAIL)
    payload = {"email": EMAIL, "password": TEST_PASSWORD}
    assert client.post(path, json={"email": EMAIL}).status_code == 422
    assert (
        client.post(path, json=payload, headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    assert (
        client.post(path, json=payload, headers={"Origin": settings.frontend_base_url}).status_code
        == 200
    )
    monkeypatch.setattr(settings, "dev_auth_enabled", False)
    assert client.post(path, json=payload).status_code == 404
    monkeypatch.setattr(settings, "dev_auth_enabled", True)
    monkeypatch.setattr(settings, "app_env", "production")
    assert client.post(path, json=payload).status_code == 404


def test_repeated_failed_logins_are_limited_and_success_cannot_bypass_limit(client):
    provision_local_user(client, EMAIL)
    for _ in range(5):
        assert client.post(LOGIN, json={"email": EMAIL, "password": "wrong"}).status_code == 401
    limited = client.post(LOGIN, json={"email": EMAIL, "password": TEST_PASSWORD})
    assert limited.status_code == 429
    assert 1 <= int(limited.headers["retry-after"]) <= 301
    local_login_limiter.clear()
    assert client.post(LOGIN, json={"email": EMAIL, "password": TEST_PASSWORD}).status_code == 200


def test_limiter_reservations_are_atomic_bounded_and_success_resets_account():
    limiter = LocalLoginLimiter(capacity=4)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: limiter.reserve(EMAIL, "127.0.0.1"), range(40)))
    assert results.count(0) == 5
    limiter.succeeded(EMAIL)
    assert limiter.reserve(EMAIL, "127.0.0.1") == 0
    for index in range(20):
        limiter.reserve(f"other{index}@example.dev", "127.0.0.1")
    assert len(limiter._entries) <= 4


def test_operator_reset_preserves_identity_and_revokes_sessions(client):
    previous = sign_in(client, EMAIL, "Original Name")
    identity_id = UUID(previous["user"]["id"])
    with client.app.state.test_session_factory() as db:
        user = local_accounts.set_local_password(
            db, email=EMAIL, password="new-operator-password", display_name="Cannot Rename"
        )
        assert user.id == identity_id
        assert user.display_name == "Original Name"
        assert user.is_platform_admin is True
        assert not db.scalars(select(UserSession)).all()
        assert (
            db.scalar(
                select(AuditEvent).where(AuditEvent.action == "identity.local_password.reset")
            )
            is not None
        )
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post(LOGIN, json={"email": EMAIL, "password": TEST_PASSWORD}).status_code == 401
    assert (
        client.post(LOGIN, json={"email": EMAIL, "password": "new-operator-password"}).status_code
        == 200
    )


def test_operator_can_set_password_on_legacy_account_without_claiming_it_anonymously(client):
    with client.app.state.test_session_factory() as db:
        user = User(issuer=LOCAL_ISSUER, subject=EMAIL, email=EMAIL, display_name="Legacy")
        db.add(user)
        db.commit()
        identity_id = user.id
        local_accounts.set_local_password(db, email=EMAIL, password=TEST_PASSWORD)
        assert user.id == identity_id
    assert client.post(LOGIN, json={"email": EMAIL, "password": TEST_PASSWORD}).status_code == 200


def test_operator_requires_explicit_create_and_refuses_oidc_or_disabled_accounts(client):
    with client.app.state.test_session_factory() as db:
        with pytest.raises(ValueError, match="--create"):
            local_accounts.set_local_password(db, email=EMAIL, password=TEST_PASSWORD)
        user = local_accounts.set_local_password(
            db, email=EMAIL, password=TEST_PASSWORD, create=True
        )
        assert user.is_platform_admin is True
        user.active = False
        db.commit()
        with pytest.raises(ValueError, match="disabled"):
            local_accounts.set_local_password(db, email=EMAIL, password=TEST_PASSWORD)
        db.add(
            User(
                issuer="https://identity.example.dev",
                subject="subject",
                email="sso@example.dev",
                display_name="SSO User",
            )
        )
        db.commit()
        with pytest.raises(ValueError, match="organization identity"):
            local_accounts.set_local_password(
                db, email="sso@example.dev", password=TEST_PASSWORD, create=True
            )


def test_operator_cli_uses_hidden_confirmation_and_does_not_accept_password_argument(
    client, monkeypatch, capsys
):
    passwords = iter([TEST_PASSWORD, TEST_PASSWORD])
    prompts = []

    def prompt(message):
        prompts.append(message)
        return next(passwords)

    monkeypatch.setattr(local_accounts.getpass, "getpass", prompt)
    monkeypatch.setattr(local_accounts, "SessionLocal", client.app.state.test_session_factory)
    assert local_accounts.main(["set-password", "--email", EMAIL, "--create"]) == 0
    assert len(prompts) == 2
    assert TEST_PASSWORD not in capsys.readouterr().out
    assert client.post(LOGIN, json={"email": EMAIL, "password": TEST_PASSWORD}).status_code == 200
    with pytest.raises(SystemExit):
        local_accounts.main(["set-password", "--email", EMAIL, "--password", TEST_PASSWORD])


def test_operator_cli_mismatched_confirmation_leaves_database_unchanged(client, monkeypatch):
    passwords = iter([TEST_PASSWORD, "different-password"])
    monkeypatch.setattr(local_accounts.getpass, "getpass", lambda _: next(passwords))
    monkeypatch.setattr(local_accounts, "SessionLocal", client.app.state.test_session_factory)
    assert local_accounts.main(["set-password", "--email", EMAIL, "--create"]) == 1
    with client.app.state.test_session_factory() as db:
        assert not db.scalars(select(User)).all()


def test_operator_provisioning_is_disabled_outside_development(client, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    with client.app.state.test_session_factory() as db:
        with pytest.raises(ValueError, match="development auth"):
            local_accounts.set_local_password(db, email=EMAIL, password=TEST_PASSWORD, create=True)


def test_password_migration_preserves_users_and_oidc_sessions_only():
    migration = import_module("migrations.versions.d7f2c9a1e630_local_account_passwords")
    engine = create_engine("sqlite://")
    local_id, oidc_id = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id VARCHAR PRIMARY KEY, issuer VARCHAR)"))
        connection.execute(text("CREATE TABLE user_sessions (id VARCHAR, user_id VARCHAR)"))
        connection.execute(
            text("INSERT INTO users VALUES (:id, :issuer)"),
            [
                {"id": local_id, "issuer": LOCAL_ISSUER},
                {"id": oidc_id, "issuer": "https://identity.example.dev"},
            ],
        )
        connection.execute(
            text("INSERT INTO user_sessions VALUES (:id, :user_id)"),
            [
                {"id": "old-development-session", "user_id": local_id},
                {"id": "oidc-session", "user_id": oidc_id},
            ],
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert {row[0] for row in connection.execute(text("SELECT id FROM users"))} == {
            local_id,
            oidc_id,
        }
        assert connection.execute(text("SELECT id FROM user_sessions")).scalars().all() == [
            "oidc-session"
        ]
        column = next(
            c for c in inspect(connection).get_columns("users") if c["name"] == "password_hash"
        )
        assert column["nullable"]
        assert connection.execute(text("SELECT password_hash FROM users")).scalars().all() == [
            None,
            None,
        ]
    engine.dispose()
