from fastapi.testclient import TestClient


def test_liveness_endpoint(client: TestClient):
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_ready_endpoint_checks_database(client: TestClient):
    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "healthy"}


def test_system_status_reports_starting_worker_without_heartbeat(client: TestClient):
    login = client.post(
        "/api/v1/auth/dev-login",
        json={"email": "platform-operator@example.dev", "display_name": "Platform Operator"},
    )
    assert login.status_code == 200
    response = client.get("/api/v1/system/status")

    assert response.status_code == 200
    assert response.json()["api"] == "healthy"
    assert response.json()["database"] == "healthy"
    assert response.json()["worker"] == "starting"


def test_system_status_requires_an_authenticated_operator(client: TestClient):
    response = client.get("/api/v1/system/status")

    assert response.status_code == 401


def test_api_responses_include_security_headers_and_bound_request_ids(client: TestClient):
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": "bad request id"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] != "bad request id"
    assert len(response.headers["x-request-id"]) == 36
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert response.headers["cache-control"] == "no-store"


def test_validation_errors_do_not_echo_submitted_values(client: TestClient):
    response = client.post("/api/v1/auth/dev-login", json={"email": "validation-secret"})

    assert response.status_code == 422
    assert "validation-secret" not in response.text
    assert "input" not in response.text
