from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient


def sign_in(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/auth/dev-login",
        json={"email": "observability@example.dev", "display_name": "SRE Engineer"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def setup_workspace(client: TestClient) -> tuple[dict, dict, dict, dict]:
    session = sign_in(client)
    headers = {"X-CSRF-Token": session["csrf_token"]}
    workspace_response = client.post(
        "/api/v1/workspaces",
        json={"name": "Observability Workspace", "slug": "observability"},
        headers=headers,
    )
    assert workspace_response.status_code == 201, workspace_response.text
    workspace = workspace_response.json()
    base = f"/api/v1/workspaces/{workspace['id']}"
    team_response = client.post(
        f"{base}/teams",
        json={"slug": "checkout", "name": "Checkout Team"},
        headers=headers,
    )
    assert team_response.status_code == 201, team_response.text
    service_response = client.post(
        f"{base}/services",
        json={
            "slug": "checkout",
            "name": "Checkout API",
            "owner_team_id": team_response.json()["id"],
        },
        headers=headers,
    )
    assert service_response.status_code == 201, service_response.text
    service = service_response.json()
    environment_response = client.post(
        f"{base}/environments",
        json={"slug": "production", "name": "Production", "environment_type": "production"},
        headers=headers,
    )
    assert environment_response.status_code == 201, environment_response.text
    environment = environment_response.json()
    source_response = client.post(
        f"{base}/telemetry-sources",
        json={
            "name": "checkout production exporter",
            "service_id": service["id"],
            "environment_id": environment["id"],
        },
        headers=headers,
    )
    assert source_response.status_code == 201, source_response.text
    return session, workspace, service, {**source_response.json(), "headers": headers}


def test_metric_source_is_scoped_hashed_revocable_and_idempotent(client):
    _session, _workspace, _service, source = setup_workspace(client)
    token = source["token"]
    path = "/api/v1/telemetry/samples"
    sample = {
        "idempotency_key": "interval-0001",
        "observed_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        "interval_seconds": 60,
        "request_count": 100,
        "error_count": 2,
        "latency_bucket_counts": {"100": 20, "300": 70, "500": 90, "1000": 95},
    }
    rejected = client.post(path, json={"samples": [sample]})
    assert rejected.status_code == 401

    headers = {"Authorization": f"Bearer {token}"}
    ingested = client.post(path, json={"samples": [sample]}, headers=headers)
    assert ingested.status_code == 200, ingested.text
    assert ingested.json() == {"accepted": 1, "duplicates": 0}
    duplicate = client.post(path, json={"samples": [sample]}, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json() == {"accepted": 0, "duplicates": 1}
    conflicting_retry = client.post(
        path,
        json={"samples": [{**sample, "request_count": 101}]},
        headers=headers,
    )
    assert conflicting_retry.status_code == 409

    listed = client.get(f"/api/v1/workspaces/{source['workspace_id']}/telemetry-sources")
    assert listed.status_code == 200
    assert "token" not in listed.json()[0]

    disabled = client.patch(
        f"/api/v1/workspaces/{source['workspace_id']}/telemetry-sources/{source['id']}",
        json={"enabled": False},
        headers=source["headers"],
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    denied = client.post(path, json={"samples": [sample]}, headers=headers)
    assert denied.status_code == 401


def test_slo_evaluation_fires_acknowledges_and_recovers_alert(client):
    _session, workspace, service, source = setup_workspace(client)
    base = f"/api/v1/workspaces/{workspace['id']}"
    created = client.post(
        f"{base}/slos",
        json={
            "slug": "checkout-availability",
            "name": "Checkout availability",
            "service_id": service["id"],
            "environment_id": source["environment_id"],
            "indicator": "availability",
            "objective": 0.99,
            "window_hours": 24,
        },
        headers=source["headers"],
    )
    assert created.status_code == 201, created.text
    slo = created.json()
    sample_time = datetime.now(UTC) - timedelta(minutes=1)
    telemetry = client.post(
        "/api/v1/telemetry/samples",
        headers={"Authorization": f"Bearer {source['token']}"},
        json={
            "samples": [
                {
                    "idempotency_key": "bad-window-01",
                    "observed_at": sample_time.isoformat(),
                    "interval_seconds": 60,
                    "request_count": 1000,
                    "error_count": 20,
                }
            ]
        },
    )
    assert telemetry.status_code == 200, telemetry.text

    evaluation = client.post(
        f"{base}/slos/{slo['id']}/evaluate", headers=source["headers"]
    )
    assert evaluation.status_code == 201, evaluation.text
    body = evaluation.json()
    assert body["status"] == "breaching"
    assert body["attainment"] == 0.98
    assert body["burn_rate"] == 2.0

    firing = client.get(f"{base}/alerts?status=firing")
    assert firing.status_code == 200
    assert len(firing.json()) == 1
    alert = firing.json()[0]
    acknowledged = client.post(
        f"{base}/alerts/{alert['id']}/acknowledge", headers=source["headers"]
    )
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["status"] == "acknowledged"

    recovered = client.post(
        "/api/v1/telemetry/samples",
        headers={"Authorization": f"Bearer {source['token']}"},
        json={
            "samples": [
                {
                    "idempotency_key": "recovery-window-01",
                    "observed_at": (sample_time + timedelta(seconds=60)).isoformat(),
                    "interval_seconds": 60,
                    "request_count": 2000,
                    "error_count": 0,
                }
            ]
        },
    )
    assert recovered.status_code == 200, recovered.text
    healthy = client.post(
        f"{base}/slos/{slo['id']}/evaluate", headers=source["headers"]
    )
    assert healthy.status_code == 201, healthy.text
    assert healthy.json()["status"] == "healthy"
    resolved = client.get(f"{base}/alerts?status=resolved")
    assert resolved.status_code == 200
    assert resolved.json()[0]["id"] == alert["id"]


def test_latency_slo_needs_matching_histogram_bucket(client):
    _session, workspace, service, source = setup_workspace(client)
    base = f"/api/v1/workspaces/{workspace['id']}"
    created = client.post(
        f"{base}/slos",
        json={
            "slug": "checkout-latency",
            "name": "Checkout p95 latency",
            "service_id": service["id"],
            "indicator": "latency",
            "objective": 0.95,
            "window_hours": 1,
            "latency_threshold_ms": 300,
        },
        headers=source["headers"],
    )
    assert created.status_code == 201, created.text
    slo_id = created.json()["id"]
    ingested = client.post(
        "/api/v1/telemetry/samples",
        headers={"Authorization": f"Bearer {source['token']}"},
        json={
            "samples": [
                {
                    "idempotency_key": "latency-window-01",
                    "observed_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
                    "interval_seconds": 60,
                    "request_count": 100,
                    "error_count": 0,
                    "latency_bucket_counts": {"100": 30, "300": 90, "500": 98, "1000": 100},
                }
            ]
        },
    )
    assert ingested.status_code == 200, ingested.text
    result = client.post(f"{base}/slos/{slo_id}/evaluate", headers=source["headers"])
    assert result.status_code == 201, result.text
    assert result.json()["status"] == "breaching"
    assert result.json()["attainment"] == 0.9


def test_telemetry_rejects_invalid_histogram_and_future_samples(client):
    _session, _workspace, _service, source = setup_workspace(client)
    headers = {"Authorization": f"Bearer {source['token']}"}
    invalid_histogram = client.post(
        "/api/v1/telemetry/samples",
        headers=headers,
        json={
            "samples": [
                {
                    "idempotency_key": "invalid-histogram",
                    "observed_at": datetime.now(UTC).isoformat(),
                    "interval_seconds": 60,
                    "request_count": 10,
                    "error_count": 0,
                    "latency_bucket_counts": {"100": 8, "300": 4},
                }
            ]
        },
    )
    assert invalid_histogram.status_code == 422

    future_sample = client.post(
        "/api/v1/telemetry/samples",
        headers=headers,
        json={
            "samples": [
                {
                    "idempotency_key": "future-sample-01",
                    "observed_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "interval_seconds": 60,
                    "request_count": 10,
                    "error_count": 0,
                }
            ]
        },
    )
    assert future_sample.status_code == 422
