"""Independent verification of signed build evidence before production promotion."""

import hashlib
import json
import re
from functools import lru_cache
from urllib.parse import urlsplit

from sigstore.errors import VerificationError
from sigstore.hashes import Hashed
from sigstore.models import Bundle, InvalidBundle
from sigstore.verify import Verifier
from sigstore.verify.policy import (
    AllOf,
    GitHubWorkflowRepository,
    GitHubWorkflowSHA,
    GitHubWorkflowTrigger,
    Identity,
)
from sigstore_models.common.v1 import HashAlgorithm

from app.integrations.github_actions import GitHubDispatchError, parse_github_repository
from app.models import BuildRun
from app.schemas.core import BuildArtifactEvidence, BuildCallbackRequest

GITHUB_ACTIONS_ISSUER = "https://token.actions.githubusercontent.com"
SLSA_PROVENANCE_V1 = "https://slsa.dev/provenance/v1"
IN_TOTO_STATEMENT_V1 = "https://in-toto.io/Statement/v1"


class SupplyChainVerificationRejected(Exception):
    """The evidence is invalid, incomplete, or does not match this release."""


class SupplyChainVerificationUnavailable(Exception):
    """The evidence verifier could not reach or initialize its trust services."""


@lru_cache(maxsize=1)
def _production_verifier() -> Verifier:
    return Verifier.production()


def _expected_workflow_identity(build_run: BuildRun, evidence: BuildArtifactEvidence) -> str:
    if not build_run.repository_url or not build_run.commit_sha:
        raise SupplyChainVerificationRejected("release source metadata is incomplete")
    try:
        owner, repository = parse_github_repository(build_run.repository_url)
    except GitHubDispatchError as exc:
        raise SupplyChainVerificationRejected("release repository is not supported") from exc

    source_ref = build_run.source_ref.strip()
    if re.fullmatch(r"[0-9a-f]{40,64}", source_ref):
        raise SupplyChainVerificationRejected(
            "verified production releases require a named branch or tag ref"
        )
    if source_ref.startswith("refs/heads/") or source_ref.startswith("refs/tags/"):
        workflow_ref = source_ref
    elif source_ref.startswith("refs/") or not source_ref:
        raise SupplyChainVerificationRejected("release ref must be a branch or tag")
    else:
        workflow_ref = f"refs/heads/{source_ref}"

    workflow_filename = build_run.workflow_path.rsplit("/", 1)[-1]
    expected = (
        f"https://github.com/{owner}/{repository}/.github/workflows/"
        f"{workflow_filename}@{workflow_ref}"
    )
    if evidence.signer_identity != expected:
        raise SupplyChainVerificationRejected(
            "reported signer identity does not match the configured workflow and release ref"
        )
    if evidence.oidc_issuer != GITHUB_ACTIONS_ISSUER:
        raise SupplyChainVerificationRejected("reported OIDC issuer is not GitHub Actions")
    return expected


def _verification_policy(
    build_run: BuildRun,
    evidence: BuildArtifactEvidence,
    commit_sha: str,
) -> AllOf:
    owner, repository = parse_github_repository(build_run.repository_url or "")
    expected_identity = _expected_workflow_identity(build_run, evidence)
    return AllOf(
        [
            Identity(identity=expected_identity, issuer=GITHUB_ACTIONS_ISSUER),
            GitHubWorkflowRepository(f"{owner}/{repository}"),
            GitHubWorkflowSHA(commit_sha),
            GitHubWorkflowTrigger("workflow_dispatch"),
        ]
    )


def _bundle(bundle_json: dict | None, label: str) -> Bundle:
    if not bundle_json:
        raise SupplyChainVerificationRejected(f"{label} bundle is required")
    try:
        return Bundle.from_json(json.dumps(bundle_json, separators=(",", ":")))
    except (InvalidBundle, ValueError, TypeError) as exc:
        raise SupplyChainVerificationRejected(f"{label} bundle is invalid") from exc


def _resource_matches_source(resource: object, repository_url: str, commit_sha: str) -> bool:
    if not isinstance(resource, dict):
        return False
    uri = resource.get("uri")
    digest = resource.get("digest")
    if not isinstance(uri, str) or not isinstance(digest, dict):
        return False
    normalized_uri = uri.removeprefix("git+")
    source_uri = urlsplit(normalized_uri)
    expected = urlsplit(repository_url)
    source_path = source_uri.path.split("@", 1)[0].rstrip("/").removesuffix(".git")
    expected_path = expected.path.rstrip("/").removesuffix(".git")
    return (
        source_uri.hostname == "github.com"
        and expected.hostname == "github.com"
        and source_path.casefold() == expected_path.casefold()
        and str(digest.get("gitCommit", "")).casefold() == commit_sha.casefold()
    )


def _verify_provenance_statement(
    *,
    content_type: str,
    statement_bytes: bytes,
    evidence: BuildArtifactEvidence,
    repository_url: str,
    commit_sha: str,
) -> None:
    if not content_type.startswith("application/vnd.in-toto+json"):
        raise SupplyChainVerificationRejected("provenance payload is not an in-toto statement")
    if evidence.provenance_digest != f"sha256:{hashlib.sha256(statement_bytes).hexdigest()}":
        raise SupplyChainVerificationRejected("provenance payload digest does not match its report")
    try:
        statement = json.loads(statement_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SupplyChainVerificationRejected("provenance statement is not valid JSON") from exc
    if (
        not isinstance(statement, dict)
        or statement.get("_type") != IN_TOTO_STATEMENT_V1
        or statement.get("predicateType") != SLSA_PROVENANCE_V1
    ):
        raise SupplyChainVerificationRejected("provenance must use in-toto v1 and SLSA v1")

    expected_digest = evidence.digest.removeprefix("sha256:").casefold()
    subjects = statement.get("subject")
    if not isinstance(subjects, list) or not any(
        isinstance(subject, dict)
        and isinstance(subject.get("digest"), dict)
        and str(subject["digest"].get("sha256", "")).casefold() == expected_digest
        for subject in subjects
    ):
        raise SupplyChainVerificationRejected(
            "provenance subject does not identify the reported artifact digest"
        )

    predicate = statement.get("predicate")
    if not isinstance(predicate, dict):
        raise SupplyChainVerificationRejected("provenance predicate is missing")
    build_definition = predicate.get("buildDefinition")
    if not isinstance(build_definition, dict):
        raise SupplyChainVerificationRejected("provenance build definition is missing")
    parameters = build_definition.get("externalParameters", {})
    resources = build_definition.get("resolvedDependencies", [])
    candidates = list(resources) if isinstance(resources, list) else []
    if isinstance(parameters, dict) and isinstance(parameters.get("source"), dict):
        candidates.append(parameters["source"])
    if not any(
        _resource_matches_source(item, repository_url, commit_sha) for item in candidates
    ):
        raise SupplyChainVerificationRejected(
            "provenance does not bind the configured repository to the built commit"
        )


def verify_callback_evidence(payload: BuildCallbackRequest, build_run: BuildRun) -> None:
    """Verify every reported artifact signature and its SLSA provenance bundle."""
    if payload.status != "succeeded":
        return
    if not payload.artifacts:
        raise SupplyChainVerificationRejected("successful builds must include artifact evidence")
    try:
        verifier = _production_verifier()
        for evidence in payload.artifacts:
            policy = _verification_policy(build_run, evidence, payload.commit_sha)
            signature_bundle = _bundle(evidence.signature_bundle, "artifact signature")
            digest = bytes.fromhex(evidence.digest.removeprefix("sha256:"))
            verifier.verify_artifact(
                Hashed(algorithm=HashAlgorithm.SHA2_256, digest=digest),
                signature_bundle,
                policy,
            )

            provenance_bundle = _bundle(evidence.provenance_bundle, "SLSA provenance")
            content_type, statement_bytes = verifier.verify_dsse(provenance_bundle, policy)
            _verify_provenance_statement(
                content_type=content_type,
                statement_bytes=statement_bytes,
                evidence=evidence,
                repository_url=build_run.repository_url or "",
                commit_sha=payload.commit_sha,
            )
    except SupplyChainVerificationRejected:
        raise
    except (InvalidBundle, VerificationError, ValueError, TypeError) as exc:
        raise SupplyChainVerificationRejected("signed build evidence failed verification") from exc
    except Exception as exc:
        raise SupplyChainVerificationUnavailable(
            "Sigstore trust verification is temporarily unavailable"
        ) from exc
