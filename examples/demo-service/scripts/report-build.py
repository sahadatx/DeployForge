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
from urllib.parse import quote, urlsplit, urlunsplit


def read_json(path: str) -> tuple[dict, bytes]:
    raw = Path(path).read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value, raw


def provenance_payload_digest(bundle: dict) -> str:
    envelope = bundle.get("dsseEnvelope")
    if not isinstance(envelope, dict) or not isinstance(envelope.get("payload"), str):
        raise TypeError("Provenance bundle does not contain a DSSE payload")
    statement = base64.b64decode(envelope["payload"], validate=True)
    return "sha256:" + hashlib.sha256(statement).hexdigest()


def vulnerability_summary(path: str) -> dict[str, int]:
    report, _ = read_json(path)
    summary = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    matches = report.get("matches", [])
    if not isinstance(matches, list):
        raise TypeError("Grype report matches must be a list")
    for match in matches:
        vulnerability = (
            match.get("vulnerability", {}) if isinstance(match, dict) else {}
        )
        severity = str(vulnerability.get("severity", "unknown")).casefold()
        if severity == "negligible":
            severity = "low"
        if severity not in summary:
            severity = "unknown"
        summary[severity] += 1
    return summary


def canonical_json_digest(value: dict) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def prepare_scanner_predicate() -> int:
    report, report_bytes = read_json(os.environ["VULNERABILITY_REPORT_PATH"])
    descriptor = report.get("descriptor", {})
    if (
        not isinstance(descriptor, dict)
        or str(descriptor.get("name", "")).casefold() != "grype"
    ):
        raise ValueError("The vulnerability report was not produced by Grype")
    version = str(descriptor.get("version", "")).strip()
    image_digest = os.environ.get("IMAGE_DIGEST", "").strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest) or not version:
        raise ValueError("The exact image digest and Grype version are required")
    predicate = {
        "schemaVersion": 1,
        "scanner": "grype",
        "scannerVersion": version,
        "reportDigest": "sha256:" + hashlib.sha256(report_bytes).hexdigest(),
        "summary": vulnerability_summary(os.environ["VULNERABILITY_REPORT_PATH"]),
    }
    Path("vulnerability.predicate.json").write_text(
        json.dumps(predicate, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


def build_payload() -> dict:
    status = os.environ.get("BUILD_STATUS", "failure").casefold()
    succeeded = status in {"success", "succeeded", "succeededwithissues"}
    gitlab_project_path = os.environ.get("CI_PROJECT_PATH", "").strip()
    is_gitlab = bool(gitlab_project_path and not os.environ.get("GITHUB_REPOSITORY"))
    is_azure_devops = bool(
        os.environ.get("SYSTEM_COLLECTIONURI")
        and os.environ.get("SYSTEM_TEAMPROJECT")
        and os.environ.get("BUILD_REPOSITORY_NAME")
        and not gitlab_project_path
        and not os.environ.get("GITHUB_REPOSITORY")
    )
    commit_sha = (
        os.environ.get(
            "SOURCE_COMMIT_SHA",
            os.environ.get("CI_COMMIT_SHA", os.environ.get("BUILD_SOURCEVERSION", "")),
        )
        .strip()
        .lower()
    )
    if len(commit_sha) not in (40, 64) or any(
        char not in "0123456789abcdef" for char in commit_sha
    ):
        raise ValueError("A resolved source commit SHA is required for the callback")

    if is_gitlab:
        repository = os.environ["CI_PROJECT_URL"].rstrip("/")
        workflow_path = os.environ.get("CI_CONFIG_PATH", ".gitlab-ci.yml").strip()
        server_url = os.environ.get("CI_SERVER_URL", "").rstrip("/")
        pipeline_id = os.environ["CI_PIPELINE_ID"]
        run_url = os.environ["CI_PIPELINE_URL"]
        if os.environ.get("CI_COMMIT_TAG"):
            identity_ref = f"refs/tags/{os.environ['CI_COMMIT_TAG']}"
        else:
            identity_ref = f"refs/heads/{os.environ['CI_COMMIT_REF_NAME']}"
        repository_path = f"{server_url}/{gitlab_project_path}"
        signer_identity = f"{repository_path}//{workflow_path}@{identity_ref}"
        oidc_issuer = server_url
    elif is_azure_devops:
        collection = urlsplit(os.environ["SYSTEM_COLLECTIONURI"].strip())
        project = os.environ["SYSTEM_TEAMPROJECT"].strip()
        repository_name = os.environ["BUILD_REPOSITORY_NAME"].strip()
        pipeline_id = os.environ["BUILD_BUILDID"].strip()
        if (
            collection.scheme != "https"
            or collection.hostname != "dev.azure.com"
            or collection.username
            or collection.password
            or collection.query
            or collection.fragment
            or not re.fullmatch(r"[0-9]+", pipeline_id)
            or any(
                not value
                or value in {".", ".."}
                or any(ord(char) < 32 for char in value)
                for value in (project, repository_name)
            )
        ):
            raise ValueError("Azure DevOps pipeline metadata is incomplete or invalid")
        collection_path = collection.path.rstrip("/")
        repository_path = (
            f"{collection_path}/{quote(project, safe='')}/_git/"
            f"{quote(repository_name, safe='')}"
        )
        repository = urlunsplit(
            (collection.scheme, collection.netloc, repository_path, "", "")
        )
        source_ref = os.environ.get("BUILD_SOURCEBRANCH", "").strip()
        if not source_ref.startswith(("refs/heads/", "refs/tags/")):
            raise ValueError("Azure DevOps did not provide a branch or tag ref")
        identity_ref = source_ref
        run_url = urlunsplit(
            (
                collection.scheme,
                collection.netloc,
                f"{collection_path}/{quote(project, safe='')}/_build/results",
                f"buildId={pipeline_id}",
                "",
            )
        )
        signer_identity = os.environ.get("DEPLOYFORGE_SIGNER_IDENTITY", "").strip()
        oidc_issuer = os.environ.get("DEPLOYFORGE_OIDC_ISSUER", "").strip()
        if not signer_identity or not oidc_issuer:
            raise ValueError(
                "Azure DevOps signing requires the dispatched signer identity and OIDC issuer variables"
            )
    else:
        repository = os.environ["GITHUB_REPOSITORY"]
        workflow_path = ".github/workflows/deployforge-build.yml"
        workflow_ref = os.environ["GITHUB_REF"]
        if workflow_ref.startswith("refs/"):
            identity_ref = workflow_ref
        else:
            raise ValueError("GitHub did not provide a branch or tag workflow ref")
        pipeline_id = os.environ["GITHUB_RUN_ID"]
        run_url = (
            f"{os.environ['GITHUB_SERVER_URL']}/{repository}/actions/runs/{pipeline_id}"
        )
        signer_identity = (
            f"https://github.com/{repository}/.github/workflows/{Path(workflow_path).name}"
            f"@{identity_ref}"
        )
        oidc_issuer = "https://token.actions.githubusercontent.com"

    payload: dict = {
        "delivery_id": (
            f"{pipeline_id}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}-{uuid.uuid4()}"
        ),
        "repository_url": (
            repository
            if is_gitlab or is_azure_devops
            else f"{os.environ['GITHUB_SERVER_URL']}/{repository}.git"
        ),
        "external_run_id": pipeline_id,
        "run_url": run_url,
        "commit_sha": commit_sha,
        "status": "succeeded" if succeeded else "failed",
    }
    if not succeeded:
        payload["failure_message"] = (
            "Build, vulnerability scan, signing, or attestation failed. "
            "Inspect the associated CI run for the failing step."
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
    sbom, _ = read_json(os.environ["SBOM_PATH"])
    vulnerability_report, vulnerability_report_bytes = read_json(
        os.environ["VULNERABILITY_REPORT_PATH"]
    )
    scanner_descriptor = vulnerability_report.get("descriptor", {})
    if not isinstance(scanner_descriptor, dict):
        raise TypeError("Grype descriptor must be a JSON object")
    scanner_version = str(scanner_descriptor.get("version", "")).strip()
    if not scanner_version:
        raise ValueError("Grype scanner version is missing")
    signature_bundle, _ = read_json(os.environ["SIGNATURE_BUNDLE_PATH"])
    provenance_bundle, _ = read_json(os.environ["PROVENANCE_BUNDLE_PATH"])
    sbom_bundle, _ = read_json(os.environ["SBOM_BUNDLE_PATH"])
    vulnerability_bundle, _ = read_json(os.environ["VULNERABILITY_BUNDLE_PATH"])
    for label, bundle in (
        ("Signature", signature_bundle),
        ("Provenance", provenance_bundle),
    ):
        if len(json.dumps(bundle, separators=(",", ":")).encode("utf-8")) > 100 * 1024:
            raise ValueError(
                f"{label} bundle exceeds DeployForge's per-bundle size limit"
            )
    if (
        len(json.dumps(vulnerability_bundle, separators=(",", ":")).encode("utf-8"))
        > 256 * 1024
    ):
        raise ValueError(
            "Vulnerability scan bundle exceeds DeployForge's 256 KiB limit"
        )
    if (
        len(json.dumps(sbom_bundle, separators=(",", ":")).encode("utf-8"))
        > 6 * 1024 * 1024
    ):
        raise ValueError("SBOM attestation bundle exceeds DeployForge's 6 MiB limit")
    if sbom.get("bomFormat") != "CycloneDX":
        raise ValueError("The generated SBOM is not CycloneDX JSON")

    payload["artifacts"] = [
        {
            "name": "deployforge-demo-service",
            "artifact_type": "container",
            "uri": f"oci://{image}@{image_digest}",
            "digest": image_digest,
            "sbom_format": "cyclonedx-json",
            "sbom_uri": os.environ.get("SBOM_URI", "").strip() or run_url,
            "sbom_digest": canonical_json_digest(sbom),
            "sbom_bundle": sbom_bundle,
            "vulnerability_uri": os.environ.get("VULNERABILITY_URI", "").strip()
            or run_url,
            "vulnerability_summary": vulnerability_summary(
                os.environ["VULNERABILITY_REPORT_PATH"]
            ),
            "vulnerability_report_digest": "sha256:"
            + hashlib.sha256(vulnerability_report_bytes).hexdigest(),
            "vulnerability_scanner_version": scanner_version,
            "vulnerability_evidence_bundle": vulnerability_bundle,
            "signature_uri": run_url,
            "signature_bundle": signature_bundle,
            "signer_identity": signer_identity,
            "oidc_issuer": oidc_issuer,
            "provenance_uri": os.environ.get("PROVENANCE_URI", "").strip() or run_url,
            "provenance_digest": provenance_payload_digest(provenance_bundle),
            "provenance_bundle": provenance_bundle,
        }
    ]
    return payload


def validate_callback_destination(
    callback_url: str, allowed_host: str, build_run_id: str
) -> None:
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
    if "--prepare-scanner-predicate" in sys.argv[1:]:
        return prepare_scanner_predicate()
    callback_url = os.environ.get("DEPLOYFORGE_CALLBACK_URL", "").strip()
    callback_allowed_host = os.environ.get("DEPLOYFORGE_CALLBACK_ALLOWED_HOST", "")
    secret = os.environ.get("DEPLOYFORGE_CALLBACK_SECRET", "")
    build_run_id = os.environ.get(
        "BUILD_RUN_ID", os.environ.get("DEPLOYFORGE_BUILD_RUN_ID", "")
    )
    validate_callback_destination(callback_url, callback_allowed_host, build_run_id)
    if not secret:
        raise ValueError("The DeployForge callback secret is required")

    body = json.dumps(build_payload(), separators=(",", ":")).encode("utf-8")
    if len(body) > 12 * 1024 * 1024:
        raise ValueError(
            "Build evidence exceeds DeployForge's 12 MiB callback size limit"
        )
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
                delay = min(60, 2**attempt)
        except urllib.error.URLError as exc:
            if attempt == 5:
                raise RuntimeError(
                    "DeployForge callback endpoint could not be reached after retries"
                ) from exc
            delay = min(60, 2**attempt)
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
