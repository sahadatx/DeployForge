#!/usr/bin/env python3
"""Send a minimal, signed terminal build callback to DeployForge."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit


def read_json(path: str) -> tuple[dict, bytes]:
    raw = Path(path).read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value, raw


def provenance_payload_digest(bundle: dict) -> str:
    envelope = bundle.get("dsseEnvelope")
    if not isinstance(envelope, dict) or not isinstance(envelope.get("payload"), str):
        raise ValueError("Provenance bundle does not contain a DSSE payload")
    statement = base64.b64decode(envelope["payload"], validate=True)
    return "sha256:" + hashlib.sha256(statement).hexdigest()


def vulnerability_summary(path: str) -> dict[str, int]:
    report, _ = read_json(path)
    summary = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    matches = report.get("matches", [])
    if not isinstance(matches, list):
        raise ValueError("Grype report matches must be a list")
    for match in matches:
        vulnerability = match.get("vulnerability", {}) if isinstance(match, dict) else {}
        severity = str(vulnerability.get("severity", "unknown")).casefold()
        if severity == "negligible":
            severity = "low"
        if severity not in summary:
            severity = "unknown"
        summary[severity] += 1
    return summary


def build_payload() -> dict:
    status = os.environ.get("BUILD_STATUS", "failure").casefold()
    succeeded = status == "success"
    commit_sha = os.environ.get("SOURCE_COMMIT_SHA", "").strip().lower()
    if len(commit_sha) not in (40, 64) or any(
        char not in "0123456789abcdef" for char in commit_sha
    ):
        raise ValueError("A resolved source commit SHA is required for the callback")

    repository = os.environ["GITHUB_REPOSITORY"]
    workflow_path = ".github/workflows/deployforge-build.yml"
    workflow_ref = os.environ["GITHUB_REF"]
    if workflow_ref.startswith("refs/"):
        identity_ref = workflow_ref
    else:
        raise ValueError("GitHub did not provide a branch or tag workflow ref")
    run_url = (
        f"{os.environ['GITHUB_SERVER_URL']}/{repository}/actions/runs/"
        f"{os.environ['GITHUB_RUN_ID']}"
    )

    payload: dict = {
        "delivery_id": (
            f"{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}-"
            f"{uuid.uuid4()}"
        ),
        "repository_url": f"{os.environ['GITHUB_SERVER_URL']}/{repository}.git",
        "external_run_id": os.environ["GITHUB_RUN_ID"],
        "run_url": run_url,
        "commit_sha": commit_sha,
        "status": "succeeded" if succeeded else "failed",
    }
    if not succeeded:
        payload["failure_message"] = (
            "Build, vulnerability scan, signing, or attestation failed. "
            "Inspect the associated GitHub Actions run for the failing step."
        )
        return payload

    image = os.environ["IMAGE"].strip()
    image_digest = os.environ["IMAGE_DIGEST"].strip()
    gitops_revision = os.environ.get("GITOPS_REVISION", "").strip().lower()
    if not image or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
        raise ValueError("The immutable image name and digest are required")
    if not re.fullmatch(r"[0-9a-f]{40,64}", gitops_revision):
        raise ValueError("A resolved immutable staging GitOps commit is required")
    payload["gitops_revision"] = gitops_revision
    sbom, sbom_bytes = read_json(os.environ["SBOM_PATH"])
    signature_bundle, _ = read_json(os.environ["SIGNATURE_BUNDLE_PATH"])
    provenance_bundle, _ = read_json(os.environ["PROVENANCE_BUNDLE_PATH"])
    for label, bundle in (
        ("Signature", signature_bundle),
        ("Provenance", provenance_bundle),
    ):
        if len(json.dumps(bundle, separators=(",", ":")).encode("utf-8")) > 100 * 1024:
            raise ValueError(f"{label} bundle exceeds DeployForge's per-bundle size limit")
    if sbom.get("bomFormat") != "CycloneDX":
        raise ValueError("The generated SBOM is not CycloneDX JSON")

    signer_identity = (
        f"https://github.com/{repository}/.github/workflows/{Path(workflow_path).name}"
        f"@{identity_ref}"
    )
    payload["artifacts"] = [
        {
            "name": "deployforge-demo-service",
            "artifact_type": "container",
            "uri": f"oci://{image}@{image_digest}",
            "digest": image_digest,
            "sbom_format": "cyclonedx-json",
            "sbom_uri": run_url,
            "sbom_digest": "sha256:" + hashlib.sha256(sbom_bytes).hexdigest(),
            "vulnerability_summary": vulnerability_summary(
                os.environ["VULNERABILITY_REPORT_PATH"]
            ),
            "signature_uri": run_url,
            "signature_bundle": signature_bundle,
            "signer_identity": signer_identity,
            "oidc_issuer": "https://token.actions.githubusercontent.com",
            "provenance_uri": os.environ.get("PROVENANCE_URI", "").strip() or run_url,
            "provenance_digest": provenance_payload_digest(provenance_bundle),
            "provenance_bundle": provenance_bundle,
        }
    ]
    return payload


def validate_callback_destination(callback_url: str, allowed_host: str, build_run_id: str) -> None:
    parsed = urlsplit(callback_url.strip())
    try:
        expected_build_run_id = str(uuid.UUID(build_run_id))
        callback_port = parsed.port
    except ValueError as exc:
        raise ValueError("A valid build run ID and callback URL are required") from exc
    expected_path = f"/api/v1/build-runs/{expected_build_run_id}/callback"
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.casefold() != allowed_host.strip().casefold()
        or parsed.username
        or parsed.password
        or callback_port not in {None, 443}
        or parsed.path != expected_path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Callback must use the allowlisted HTTPS host and this build run's callback path"
        )


def main() -> int:
    callback_url = os.environ.get("DEPLOYFORGE_CALLBACK_URL", "").strip()
    callback_allowed_host = os.environ.get("DEPLOYFORGE_CALLBACK_ALLOWED_HOST", "")
    secret = os.environ.get("DEPLOYFORGE_CALLBACK_SECRET", "")
    build_run_id = os.environ.get("BUILD_RUN_ID", "")
    validate_callback_destination(callback_url, callback_allowed_host, build_run_id)
    if not secret:
        raise ValueError("The DeployForge callback secret is required")

    body = json.dumps(build_payload(), separators=(",", ":")).encode("utf-8")
    if len(body) > 256 * 1024:
        raise ValueError("Build evidence exceeds DeployForge's callback size limit")
    for attempt in range(1, 6):
        timestamp = str(int(time.time()))
        digest = hmac.new(
            secret.encode("utf-8"), timestamp.encode() + b"." + body, hashlib.sha256
        )
        request = urllib.request.Request(
            callback_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-DeployForge-Timestamp": timestamp,
                "X-DeployForge-Signature": "sha256=" + digest.hexdigest(),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if 200 <= response.status < 300:
                    print("DeployForge accepted the signed build callback.")
                    return 0
                raise RuntimeError(
                    f"DeployForge rejected the build callback: HTTP {response.status}"
                )
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 5:
                raise RuntimeError(
                    f"DeployForge rejected the build callback: HTTP {exc.code}"
                ) from exc
            retry_after = exc.headers.get("Retry-After", "")
            try:
                delay = min(60, max(1, int(retry_after)))
            except ValueError:
                delay = min(60, 2 ** attempt)
        except urllib.error.URLError as exc:
            if attempt == 5:
                raise RuntimeError(
                    "DeployForge callback endpoint could not be reached after retries"
                ) from exc
            delay = min(60, 2 ** attempt)
        print(f"DeployForge callback is temporarily unavailable; retrying in {delay}s.")
        time.sleep(delay)
    return 1


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--validate-callback"]:
            validate_callback_destination(
                os.environ.get("DEPLOYFORGE_CALLBACK_URL", ""),
                os.environ.get("DEPLOYFORGE_CALLBACK_ALLOWED_HOST", ""),
                os.environ.get("BUILD_RUN_ID", ""),
            )
            print("DeployForge callback destination is valid.")
            raise SystemExit(0)
        if sys.argv[1:]:
            raise ValueError("Unsupported command-line argument")
        raise SystemExit(main())
    except (KeyError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Build callback failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
