import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.argo_rollouts import (
    ArgoRolloutsClient,
    ArgoRolloutsConfiguration,
    ArgoRolloutsError,
    normalize_rollout_info,
)


def test_rollouts_client_uses_stepwise_promotion_and_safe_paths():
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"accepted": True})

    client = ArgoRolloutsClient(
        ArgoRolloutsConfiguration(
            api_url="https://rollouts.example.test", token="test-token-1234567890"
        ),
        transport=httpx.MockTransport(respond),
    )
    client.get_rollout_info("payments/prod", "checkout api")
    client.promote_rollout("payments/prod", "checkout api")
    client.abort_rollout("payments/prod", "checkout api")

    assert requests[0].url.raw_path == b"/api/v1/rollouts/payments%2Fprod/checkout%20api/info"
    assert requests[0].headers["Authorization"] == "Bearer test-token-1234567890"
    assert requests[1].url.path.endswith("/promote")
    assert json.loads(requests[1].content) == {
        "name": "checkout api",
        "namespace": "payments/prod",
        "full": False,
    }
    assert requests[2].url.path.endswith("/abort")


def test_rollouts_client_maps_permission_and_transient_errors():
    def respond(status: int):
        return lambda request: httpx.Response(status)

    client = ArgoRolloutsClient(
        ArgoRolloutsConfiguration(
            api_url="https://rollouts.example.test", token="test-token-1234567890"
        ),
        transport=httpx.MockTransport(respond(403)),
    )
    try:
        client.get_rollout_info("staging", "checkout")
    except ArgoRolloutsError as exc:
        assert exc.code == "argo_rollouts_permission_denied"
        assert not exc.retryable
    else:
        raise AssertionError("Expected a permission error")

    transient = ArgoRolloutsClient(
        ArgoRolloutsConfiguration(
            api_url="https://rollouts.example.test", token="test-token-1234567890"
        ),
        transport=httpx.MockTransport(respond(503)),
    )
    try:
        transient.get_rollout_info("staging", "checkout")
    except ArgoRolloutsError as exc:
        assert exc.retryable
    else:
        raise AssertionError("Expected a retryable provider error")


def test_normalized_rollout_snapshot_is_bounded_and_excludes_unreviewed_spec_data():
    payload = {
        "rollout": {
            "metadata": {"name": "checkout", "namespace": "staging"},
            "spec": {
                "template": {
                    "spec": {"containers": [{"env": [{"name": "TOKEN", "value": "secret"}]}]}
                },
                "strategy": {
                    "canary": {
                        "steps": [
                            {"setWeight": 10},
                            {"pause": {"duration": "1m"}},
                        ]
                    }
                },
            },
            "status": {
                "phase": "Paused",
                "currentStepIndex": 1,
                "pauseConditions": [
                    {"reason": "CanaryPauseStep", "startTime": "2026-11-07T00:00:00Z"}
                ],
                "replicas": 4,
                "readyReplicas": 3,
            },
        },
        "analysisRuns": [
            {
                "name": f"analysis-{index}",
                "phase": "Successful",
                "metricResults": [
                    {
                        "name": "success-rate",
                        "phase": "Successful",
                        "measurements": [
                            {"phase": "Successful", "value": "99.9", "message": "good"}
                        ],
                    }
                ],
            }
            for index in range(25)
        ],
    }
    snapshot = normalize_rollout_info(payload)
    encoded = json.dumps(snapshot)

    assert snapshot["paused"] is True
    assert snapshot["current_step_index"] == 1
    assert snapshot["current_step"]["pause_duration"] == "1m"
    assert len(snapshot["analysis_runs"]) == 20
    assert snapshot["analysis_runs"][0]["metrics"][0]["measurements"][0]["value"] == "99.9"
    assert "secret" not in encoded
    assert "containers" not in encoded


def test_production_rollouts_token_is_worker_only_and_paired_with_url():
    api_configuration = {
        "app_env": "production",
        "runtime_role": "api",
        "database_url": "postgresql://deployforge:password@db.example.test:5432/deployforge",
        "session_secret": SecretStr("s" * 40),
        "session_cookie_secure": True,
        "oidc_discovery_url": "https://identity.example.test/.well-known/openid-configuration",
        "oidc_client_id": "deployforge",
        "oidc_client_secret": SecretStr("oidc-test-secret"),
        "oidc_redirect_uri": "https://deployforge.example.test/api/v1/auth/callback",
        "build_callback_secret": SecretStr("c" * 40),
        "require_verified_supply_chain": True,
        "frontend_base_url": "https://deployforge.example.test",
        "argo_rollouts_api_url": "https://rollouts.example.test",
    }
    api_settings = Settings(**api_configuration)
    assert api_settings.argo_rollouts_configured
    with pytest.raises(ValueError, match="API must not receive the Argo Rollouts token"):
        Settings(
            **api_configuration,
            argo_rollouts_auth_token=SecretStr("rollouts-test-token-123456"),
        )

    with pytest.raises(ValueError, match="URL and auth token must be configured together"):
        Settings(
            app_env="production",
            runtime_role="worker",
            database_url="postgresql://deployforge:password@db.example.test:5432/deployforge",
            frontend_base_url="https://deployforge.example.test",
            argo_rollouts_api_url="https://rollouts.example.test",
        )
    worker_settings = Settings(
        app_env="production",
        runtime_role="worker",
        database_url="postgresql://deployforge:password@db.example.test:5432/deployforge",
        frontend_base_url="https://deployforge.example.test",
        argo_rollouts_api_url="https://rollouts.example.test",
        argo_rollouts_auth_token=SecretStr("rollouts-test-token-123456"),
    )
    assert worker_settings.argo_rollouts_credentials_configured
