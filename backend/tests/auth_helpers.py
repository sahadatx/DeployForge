from fastapi.testclient import TestClient
from sqlalchemy import select

from app.local_accounts import set_local_password
from app.models import User

TEST_PASSWORD = "test-only-local-password-2026"


def provision_local_user(client: TestClient, email: str, display_name: str | None = None) -> None:
    """Provision fixtures with operator DB access, never through anonymous login."""
    with client.app.state.test_session_factory() as db:
        user = db.scalar(select(User).where(User.email == email.casefold()))
        if user is None:
            set_local_password(
                db, email=email, password=TEST_PASSWORD, create=True, display_name=display_name
            )


def sign_in(client: TestClient, email: str, display_name: str | None = None) -> dict:
    provision_local_user(client, email, display_name)
    response = client.post(
        "/api/v1/auth/dev-login", json={"email": email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()
