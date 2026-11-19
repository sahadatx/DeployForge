"""Kubernetes API adapter for Flux Kustomization reconciliation."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

import httpx


@dataclass(frozen=True)
class FluxConfiguration:
    api_url: str
    token: str
    timeout_seconds: int = 15


class FluxError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class FluxClient:
    def __init__(
        self,
        configuration: FluxConfiguration,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.configuration = configuration
        self.transport = transport

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        merge_patch: bool = False,
    ) -> dict:
        endpoint = f"{self.configuration.api_url.rstrip('/')}{path}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.configuration.token}",
            "Content-Type": (
                "application/merge-patch+json" if merge_patch else "application/json"
            ),
            "User-Agent": "DeployForge-Control-Plane",
        }
        try:
            with httpx.Client(
                timeout=self.configuration.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.request(method, endpoint, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise FluxError(
                "flux_request_timeout", "Kubernetes API request timed out.", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise FluxError(
                "flux_api_unavailable", "Kubernetes API could not be reached.", retryable=True
            ) from exc

        if response.status_code in {200, 201, 202}:
            try:
                payload = response.json()
            except ValueError as exc:
                raise FluxError(
                    "flux_invalid_response", "Kubernetes API returned invalid JSON.", retryable=True
                ) from exc
            if not isinstance(payload, dict):
                raise FluxError(
                    "flux_invalid_response", "Kubernetes API returned an unexpected response."
                )
            return payload
        if response.status_code in {408, 409, 429} or response.status_code >= 500:
            raise FluxError(
                "flux_api_temporarily_unavailable",
                "Kubernetes API temporarily rejected the Flux request.",
                retryable=True,
            )
        if response.status_code in {401, 403}:
            raise FluxError(
                "flux_permission_denied",
                "Kubernetes API rejected the Flux service-account permission.",
            )
        if response.status_code == 404:
            raise FluxError("flux_resource_not_found", "Flux resource was not found.")
        if response.status_code == 422:
            raise FluxError(
                "flux_resource_invalid", "Kubernetes API rejected the Flux resource request."
            )
        raise FluxError(
            "flux_api_rejected",
            f"Kubernetes API rejected the Flux request with HTTP {response.status_code}.",
        )

    @staticmethod
    def _resource_path(namespace: str, resource: str, name: str) -> str:
        return (
            "/apis/"
            f"{resource}/v1/namespaces/{quote(namespace, safe='')}/"
            f"{quote(name, safe='')}"
        )

    def get_kustomization(self, namespace: str, name: str) -> dict:
        return self._request(
            "GET",
            self._resource_path("kustomize.toolkit.fluxcd.io", namespace, f"kustomizations/{name}"),
        )

    def get_git_repository(self, namespace: str, name: str) -> dict:
        return self._request(
            "GET",
            self._resource_path("source.toolkit.fluxcd.io", namespace, f"gitrepositories/{name}"),
        )

    def list_kustomizations(self, namespace: str) -> dict:
        return self._request(
            "GET",
            f"/apis/kustomize.toolkit.fluxcd.io/v1/namespaces/{quote(namespace, safe='')}"
            "/kustomizations",
        )

    def set_git_repository_commit(self, namespace: str, name: str, commit: str) -> dict:
        requested_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        return self._request(
            "PATCH",
            self._resource_path("source.toolkit.fluxcd.io", namespace, f"gitrepositories/{name}"),
            body={
                "metadata": {
                    "annotations": {"reconcile.fluxcd.io/requestedAt": requested_at}
                },
                "spec": {"ref": {"commit": commit}},
            },
            merge_patch=True,
        )

    def get_live_image_references(self, namespace: str, target_name: str) -> list[str]:
        selector = quote(f"deployforge.io/target={target_name}", safe="=")
        payload = self._request(
            "GET",
            f"/api/v1/namespaces/{quote(namespace, safe='')}/pods?labelSelector={selector}",
        )
        items = payload.get("items") or []
        if not isinstance(items, list):
            raise FluxError("flux_invalid_response", "Kubernetes returned an invalid pod list.")
        images: list[str] = []
        for pod in items:
            if not isinstance(pod, dict):
                continue
            spec = pod.get("spec") or {}
            status = pod.get("status") or {}
            if not isinstance(spec, dict) or not isinstance(status, dict):
                continue
            regular_containers = spec.get("containers") or []
            init_containers = spec.get("initContainers") or []
            regular_statuses = status.get("containerStatuses") or []
            init_statuses = status.get("initContainerStatuses") or []
            containers = [
                *(
                    regular_containers
                    if isinstance(regular_containers, list)
                    else []
                ),
                *(init_containers if isinstance(init_containers, list) else []),
            ]
            statuses = [
                *(regular_statuses if isinstance(regular_statuses, list) else []),
                *(init_statuses if isinstance(init_statuses, list) else []),
            ]
            status_by_name = {
                item.get("name"): item for item in statuses if isinstance(item, dict)
            }
            for container in containers:
                if not isinstance(container, dict):
                    continue
                name = container.get("name")
                configured_image = container.get("image")
                state = status_by_name.get(name) or {}
                image_id = str(state.get("imageID") or "")
                if not isinstance(configured_image, str):
                    continue
                digest = image_id.partition("@")[2]
                if not digest and "sha256:" in image_id:
                    digest = image_id[image_id.rfind("sha256:") :]
                if digest:
                    images.append(f"{configured_image.split('@', 1)[0]}@{digest}")
        return images

    def request_reconciliation(self, namespace: str, name: str) -> dict:
        requested_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        return self._request(
            "PATCH",
            self._resource_path("kustomize.toolkit.fluxcd.io", namespace, f"kustomizations/{name}"),
            body={
                "metadata": {
                    "annotations": {"reconcile.fluxcd.io/requestedAt": requested_at}
                }
            },
            merge_patch=True,
        )


def canonical_git_repository_url(value: str) -> tuple[str, str, int, str] | None:
    parsed = urlsplit(value.strip())
    try:
        port = parsed.port or 443
    except ValueError:
        return None
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port < 1
        or port > 65535
        or not path
        or any(ord(character) < 32 for character in path)
        or re.search(r"(?:^|/)\.{1,2}(?:/|$)", path)
    ):
        return None
    return parsed.scheme, parsed.hostname.casefold(), port, path.casefold()
