"""Credential-safe client and bounded summaries for the Argo Rollouts API."""

from dataclasses import dataclass
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class ArgoRolloutsConfiguration:
    api_url: str
    token: str
    timeout_seconds: int = 15


class ArgoRolloutsError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ArgoRolloutsClient:
    def __init__(
        self,
        configuration: ArgoRolloutsConfiguration,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.configuration = configuration
        self.transport = transport

    def _request(self, method: str, path: str, *, body: dict | None = None) -> dict:
        endpoint = f"{self.configuration.api_url.rstrip('/')}/api/v1/rollouts{path}"
        try:
            with httpx.Client(
                timeout=self.configuration.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.request(
                    method,
                    endpoint,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self.configuration.token}",
                        "Content-Type": "application/json",
                        "User-Agent": "DeployForge-Control-Plane",
                    },
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise ArgoRolloutsError(
                "argo_rollouts_request_timeout",
                "Argo Rollouts did not respond before the request timeout.",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ArgoRolloutsError(
                "argo_rollouts_unavailable", "Argo Rollouts could not be reached.", retryable=True
            ) from exc

        if response.status_code in {200, 201, 202, 204}:
            if response.status_code == 204 or not response.content:
                return {}
            try:
                payload = response.json()
            except ValueError as exc:
                raise ArgoRolloutsError(
                    "argo_rollouts_invalid_response",
                    "Argo Rollouts returned invalid JSON.",
                    retryable=True,
                ) from exc
            if not isinstance(payload, dict):
                raise ArgoRolloutsError(
                    "argo_rollouts_invalid_response",
                    "Argo Rollouts returned an unexpected response shape.",
                    retryable=True,
                )
            return payload
        if response.status_code == 404:
            raise ArgoRolloutsError("argo_rollout_not_found", "Argo Rollout was not found.")
        if response.status_code in {401, 403}:
            raise ArgoRolloutsError(
                "argo_rollouts_permission_denied",
                "Argo Rollouts rejected the configured credential or permissions.",
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ArgoRolloutsError(
                "argo_rollouts_temporarily_unavailable",
                "Argo Rollouts temporarily rejected the request.",
                retryable=True,
            )
        raise ArgoRolloutsError(
            "argo_rollouts_request_rejected",
            f"Argo Rollouts rejected the request with HTTP {response.status_code}.",
        )

    @staticmethod
    def _path(namespace: str, name: str, suffix: str = "") -> str:
        return f"/{quote(namespace, safe='')}/{quote(name, safe='')}{suffix}"

    def get_rollout_info(self, namespace: str, name: str) -> dict:
        return self._request("GET", self._path(namespace, name, "/info"))

    def promote_rollout(self, namespace: str, name: str) -> dict:
        # Never expose or use full promotion: it skips the remaining analysis gates.
        return self._request(
            "PUT",
            self._path(namespace, name, "/promote"),
            body={"name": name, "namespace": namespace, "full": False},
        )

    def abort_rollout(self, namespace: str, name: str) -> dict:
        return self._request(
            "PUT",
            self._path(namespace, name, "/abort"),
            body={"name": name, "namespace": namespace},
        )


def _text(value: object, limit: int = 240) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]


def _items(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def normalize_rollout_info(payload: dict) -> dict:
    """Keep operationally useful rollout evidence while excluding arbitrary specs/args."""
    rollout = payload.get("rollout") if isinstance(payload.get("rollout"), dict) else payload
    status = rollout.get("status") if isinstance(rollout.get("status"), dict) else {}
    spec = rollout.get("spec") if isinstance(rollout.get("spec"), dict) else {}
    metadata = rollout.get("metadata") if isinstance(rollout.get("metadata"), dict) else {}
    strategy = spec.get("strategy") if isinstance(spec.get("strategy"), dict) else {}
    canary = strategy.get("canary") if isinstance(strategy.get("canary"), dict) else {}
    steps = _items(canary.get("steps"))
    step_index = status.get("currentStepIndex")
    step_index = (
        step_index if isinstance(step_index, int) and not isinstance(step_index, bool) else None
    )
    current_step = (
        steps[step_index] if step_index is not None and 0 <= step_index < len(steps) else {}
    )
    pause_conditions = _items(status.get("pauseConditions"))
    phase = _text(status.get("phase") or rollout.get("phase"), 40)
    analysis_runs = payload.get("analysisRuns")
    if not isinstance(analysis_runs, list):
        analysis_runs = status.get("analysisRuns")
    normalized_analysis = []
    for analysis in _items(analysis_runs)[:20]:
        metrics = []
        for metric in _items(analysis.get("metricResults"))[:40]:
            measurements = []
            for measurement in _items(metric.get("measurements"))[:20]:
                measurements.append(
                    {
                        "phase": _text(measurement.get("phase"), 40),
                        "value": _text(measurement.get("value"), 120),
                        "count": measurement.get("count")
                        if isinstance(measurement.get("count"), int)
                        else None,
                        "message": _text(measurement.get("message"), 240),
                        "started_at": _text(measurement.get("startedAt"), 80),
                        "finished_at": _text(measurement.get("finishedAt"), 80),
                    }
                )
            metrics.append(
                {
                    "name": _text(metric.get("name"), 120),
                    "phase": _text(metric.get("phase"), 40),
                    "count": metric.get("count") if isinstance(metric.get("count"), int) else None,
                    "measurements": measurements,
                }
            )
        normalized_analysis.append(
            {
                "name": _text(analysis.get("name"), 180),
                "phase": _text(analysis.get("phase"), 40),
                "message": _text(analysis.get("message"), 240),
                "started_at": _text(analysis.get("startedAt"), 80),
                "finished_at": _text(analysis.get("finishedAt"), 80),
                "metrics": metrics,
            }
        )
    name = metadata.get("name") or rollout.get("name")
    return {
        "name": _text(name, 253),
        "namespace": _text(metadata.get("namespace") or rollout.get("namespace"), 180),
        "phase": phase,
        "message": _text(status.get("message"), 500),
        "paused": bool(pause_conditions) or (phase or "").casefold() == "paused",
        "current_step_index": step_index,
        "step_count": len(steps),
        "current_step": {
            "set_weight": current_step.get("setWeight")
            if isinstance(current_step.get("setWeight"), int)
            else None,
            "pause_duration": _text(
                (current_step.get("pause") or {}).get("duration")
                if isinstance(current_step.get("pause"), dict)
                else None,
                80,
            ),
            "pause_conditions": [
                {
                    "reason": _text(item.get("reason"), 120),
                    "start_time": _text(item.get("startTime"), 80),
                }
                for item in pause_conditions[:10]
            ],
        },
        "replicas": {
            key: status.get(field)
            if isinstance(status.get(field), int) and not isinstance(status.get(field), bool)
            else None
            for key, field in (
                ("replicas", "replicas"),
                ("updated", "updatedReplicas"),
                ("ready", "readyReplicas"),
                ("available", "availableReplicas"),
            )
        },
        "analysis_runs": normalized_analysis,
    }
