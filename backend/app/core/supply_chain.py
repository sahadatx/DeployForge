"""Independent verification of signed build evidence before production promotion."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from sigstore.errors import VerificationError
from sigstore.hashes import Hashed
from sigstore.models import Bundle, InvalidBundle, TrustedRoot
from sigstore.verify import Verifier
from sigstore.verify.policy import (
    AllOf,
    GitHubWorkflowRepository,
    GitHubWorkflowSHA,
    GitHubWorkflowTrigger,
    Identity,
    OIDCBuildConfigDigest,
    OIDCBuildConfigURI,
    OIDCBuildTrigger,
    OIDCSourceRepositoryDigest,
    OIDCSourceRepositoryRef,
    OIDCSourceRepositoryURI,
)
from sigstore_models.common.v1 import HashAlgorithm

from app.core.config import settings
from app.integrations.azure_devops import (
    AzurePipelineError,
    parse_azure_pipeline_reference,
    parse_azure_repository,
)
from app.integrations.github_actions import GitHubDispatchError, parse_github_repository
from app.integrations.gitlab_ci import GitLabPipelineError, parse_gitlab_repository
from app.models import BuildRun
from app.schemas.core import BuildArtifactEvidence, BuildCallbackRequest

SLSA_PROVENANCE_V1 = "https://slsa.dev/provenance/v1"
IN_TOTO_STATEMENT_V1 = "https://in-toto.io/Statement/v1"
CYCLONEDX_PREDICATE = "https://cyclonedx.org/bom"
SPDX_PREDICATE = "https://spdx.dev/Document"
GRYPE_PREDICATE = "https://deployforge.dev/attestations/grype/v1"


class SupplyChainVerificationRejected(Exception):
    """The evidence is invalid, incomplete, or does not match this release."""


class SupplyChainVerificationUnavailable(Exception):
    """The evidence verifier could not reach or initialize its trust services."""


@lru_cache(maxsize=4)
def _verifier_for_custom_trust_root(path: str, expected_sha256: str) -> Verifier:
    try:
        root_bytes = Path(path).read_bytes()
    except OSError as exc:
        raise SupplyChainVerificationUnavailable(
            "Configured Sigstore trust-root file is unavailable"
        ) from exc
    if hashlib.sha256(root_bytes).hexdigest() != expected_sha256:
        raise SupplyChainVerificationUnavailable(
            "Configured Sigstore trust-root checksum does not match"
        )
    try:
        return Verifier(trusted_root=TrustedRoot.from_file(path))
    except Exception as exc:
        raise SupplyChainVerificationUnavailable(
            "Configured Sigstore trust root could not be loaded"
        ) from exc


def _production_verifier() -> Verifier:
    if settings.sigstore_trusted_root_file:
        return _verifier_for_custom_trust_root(
            settings.sigstore_trusted_root_file,
            settings.sigstore_trusted_root_sha256,
        )
    return Verifier.production()


def _expected_workflow_identity(build_run: BuildRun, evidence: BuildArtifactEvidence) -> str:
    if not build_run.repository_url or not build_run.commit_sha:
        raise SupplyChainVerificationRejected("release source metadata is incomplete")
    try:
        if build_run.provider == "gitlab_ci":
            project_path = parse_gitlab_repository(
                build_run.repository_url, settings.gitlab_server_url
            )
            repository_path = f"{settings.gitlab_server_url.rstrip('/')}/{project_path}"
        elif build_run.provider == "azure_devops":
            project, repository = parse_azure_repository(
                build_run.repository_url, settings.azure_devops_organization_url
            )
            pinned_pipeline = parse_azure_pipeline_reference(build_run.workflow_path)
            if pinned_pipeline is None:
                raise SupplyChainVerificationRejected(
                    "Azure DevOps pipeline identity was not pinned to this release"
                )
            repository_path = (
                f"{settings.azure_devops_organization_url.rstrip('/')}/{project}/_git/{repository}"
            )
        else:
            owner, repository = parse_github_repository(build_run.repository_url)
            repository_path = f"{settings.github_server_url.rstrip('/')}/{owner}/{repository}"
    except (GitHubDispatchError, GitLabPipelineError, AzurePipelineError) as exc:
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

    if build_run.provider == "gitlab_ci":
        expected = f"{repository_path}//{build_run.workflow_path}@{workflow_ref}"
    elif build_run.provider == "azure_devops":
        pinned_pipeline = parse_azure_pipeline_reference(build_run.workflow_path)
        if pinned_pipeline is None:
            raise SupplyChainVerificationRejected(
                "Azure DevOps pipeline identity was not pinned to this release"
            )
        expected = pinned_pipeline[1]
    else:
        workflow_filename = build_run.workflow_path.rsplit("/", 1)[-1]
        expected = f"{repository_path}/.github/workflows/{workflow_filename}@{workflow_ref}"
    expected_issuer = {
        "gitlab_ci": settings.gitlab_server_url.rstrip("/"),
        "azure_devops": settings.azure_devops_oidc_issuer,
    }.get(build_run.provider, settings.sigstore_oidc_issuer)
    if evidence.signer_identity != expected:
        raise SupplyChainVerificationRejected(
            "reported signer identity does not match the configured workflow and release ref"
        )
    if evidence.oidc_issuer != expected_issuer:
        raise SupplyChainVerificationRejected("reported OIDC issuer does not match configuration")
    return expected


def _verification_policy(
    build_run: BuildRun,
    evidence: BuildArtifactEvidence,
    commit_sha: str,
) -> AllOf:
    expected_identity = _expected_workflow_identity(build_run, evidence)
    if build_run.provider == "gitlab_ci":
        project_path = parse_gitlab_repository(
            build_run.repository_url or "", settings.gitlab_server_url
        )
        repository_uri = f"{settings.gitlab_server_url.rstrip('/')}/{project_path}"
        source_ref = build_run.source_ref
        if source_ref.startswith("refs/heads/") or source_ref.startswith("refs/tags/"):
            full_ref = source_ref
        elif source_ref.startswith("refs/") or not source_ref:
            raise SupplyChainVerificationRejected("release ref must be a branch or tag")
        else:
            full_ref = f"refs/heads/{source_ref}"
        return AllOf(
            [
                Identity(
                    identity=expected_identity,
                    issuer=settings.gitlab_server_url.rstrip("/"),
                ),
                OIDCSourceRepositoryURI(repository_uri),
                OIDCSourceRepositoryDigest(commit_sha),
                OIDCSourceRepositoryRef(full_ref),
                OIDCBuildTrigger("api"),
                OIDCBuildConfigURI(expected_identity),
                OIDCBuildConfigDigest(commit_sha),
            ]
        )
    if build_run.provider == "azure_devops":
        project, repository = parse_azure_repository(
            build_run.repository_url or "", settings.azure_devops_organization_url
        )
        repository_uri = (
            f"{settings.azure_devops_organization_url.rstrip('/')}/{project}/_git/{repository}"
        )
        source_ref = build_run.source_ref
        if source_ref.startswith(("refs/heads/", "refs/tags/")):
            full_ref = source_ref
        elif source_ref.startswith("refs/") or not source_ref:
            raise SupplyChainVerificationRejected("release ref must be a branch or tag")
        else:
            full_ref = f"refs/heads/{source_ref}"
        return AllOf(
            [
                Identity(
                    identity=expected_identity,
                    issuer=settings.azure_devops_oidc_issuer,
                ),
                OIDCSourceRepositoryURI(repository_uri),
                OIDCSourceRepositoryDigest(commit_sha),
                OIDCSourceRepositoryRef(full_ref),
                OIDCBuildConfigURI(expected_identity),
                OIDCBuildConfigDigest(commit_sha),
            ]
        )
    owner, repository = parse_github_repository(build_run.repository_url or "")
    return AllOf(
        [
            Identity(identity=expected_identity, issuer=settings.sigstore_oidc_issuer),
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
        source_uri.scheme == "https"
        and expected.scheme == "https"
        and source_uri.hostname == expected.hostname
        and source_uri.port == expected.port
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
    if not any(_resource_matches_source(item, repository_url, commit_sha) for item in candidates):
        raise SupplyChainVerificationRejected(
            "provenance does not bind the configured repository to the built commit"
        )


def _verified_statement(*, content_type: str, statement_bytes: bytes, label: str) -> dict:
    if not content_type.startswith("application/vnd.in-toto+json"):
        raise SupplyChainVerificationRejected(f"{label} payload is not an in-toto statement")
    try:
        statement = json.loads(statement_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SupplyChainVerificationRejected(f"{label} statement is not valid JSON") from exc
    if not isinstance(statement, dict) or statement.get("_type") != IN_TOTO_STATEMENT_V1:
        raise SupplyChainVerificationRejected(f"{label} must use in-toto Statement v1")
    return statement


def _statement_matches_artifact(statement: dict, evidence: BuildArtifactEvidence) -> bool:
    expected_digest = evidence.digest.removeprefix("sha256:").casefold()
    subjects = statement.get("subject")
    return isinstance(subjects, list) and any(
        isinstance(subject, dict)
        and isinstance(subject.get("digest"), dict)
        and str(subject["digest"].get("sha256", "")).casefold() == expected_digest
        for subject in subjects
    )


def _canonical_json_digest(value: dict) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _verify_sbom_statement(
    *, content_type: str, statement_bytes: bytes, evidence: BuildArtifactEvidence
) -> None:
    statement = _verified_statement(
        content_type=content_type, statement_bytes=statement_bytes, label="SBOM"
    )
    expected_predicate_type = (
        CYCLONEDX_PREDICATE if evidence.sbom_format == "cyclonedx-json" else SPDX_PREDICATE
    )
    if statement.get("predicateType") != expected_predicate_type:
        raise SupplyChainVerificationRejected(
            "SBOM attestation predicate does not match its format"
        )
    if not _statement_matches_artifact(statement, evidence):
        raise SupplyChainVerificationRejected(
            "SBOM attestation does not identify the artifact digest"
        )
    predicate = statement.get("predicate")
    if not isinstance(predicate, dict):
        raise SupplyChainVerificationRejected("SBOM attestation has no document predicate")
    if evidence.sbom_format == "cyclonedx-json" and predicate.get("bomFormat") != "CycloneDX":
        raise SupplyChainVerificationRejected("attested SBOM is not a CycloneDX document")
    if evidence.sbom_format == "spdx-json" and not str(predicate.get("spdxVersion", "")).startswith(
        "SPDX-"
    ):
        raise SupplyChainVerificationRejected("attested SBOM is not an SPDX document")
    if _canonical_json_digest(predicate) != evidence.sbom_digest:
        raise SupplyChainVerificationRejected(
            "attested SBOM content digest does not match its report"
        )


def _verify_vulnerability_statement(
    *, content_type: str, statement_bytes: bytes, evidence: BuildArtifactEvidence
) -> None:
    statement = _verified_statement(
        content_type=content_type,
        statement_bytes=statement_bytes,
        label="vulnerability scan",
    )
    if statement.get("predicateType") != GRYPE_PREDICATE:
        raise SupplyChainVerificationRejected(
            "scanner attestation uses an untrusted predicate type"
        )
    if not _statement_matches_artifact(statement, evidence):
        raise SupplyChainVerificationRejected(
            "scanner attestation does not identify the artifact digest"
        )
    predicate = statement.get("predicate")
    if not isinstance(predicate, dict):
        raise SupplyChainVerificationRejected("scanner attestation has no scan result")
    if predicate.get("schemaVersion") != 1 or predicate.get("scanner") != "grype":
        raise SupplyChainVerificationRejected("vulnerability evidence was not produced by Grype")
    if (
        not isinstance(predicate.get("scannerVersion"), str)
        or not predicate["scannerVersion"].strip()
    ):
        raise SupplyChainVerificationRejected("Grype scanner version is missing")
    if predicate.get("scannerVersion") != evidence.vulnerability_scanner_version:
        raise SupplyChainVerificationRejected("Grype scanner version does not match its report")
    if predicate.get("reportDigest") != evidence.vulnerability_report_digest:
        raise SupplyChainVerificationRejected("Grype report digest does not match its attestation")
    if predicate.get("summary") != evidence.vulnerability_summary.model_dump():
        raise SupplyChainVerificationRejected(
            "Grype attested counts do not match the reported summary"
        )


def verify_callback_evidence(payload: BuildCallbackRequest, build_run: BuildRun) -> None:
    """Verify artifact signature, provenance, SBOM, and scanner evidence."""
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

            sbom_bundle = _bundle(evidence.sbom_bundle, "SBOM attestation")
            content_type, statement_bytes = verifier.verify_dsse(sbom_bundle, policy)
            _verify_sbom_statement(
                content_type=content_type,
                statement_bytes=statement_bytes,
                evidence=evidence,
            )

            if not evidence.vulnerability_report_digest:
                raise SupplyChainVerificationRejected("Grype report digest is required")
            vulnerability_bundle = _bundle(
                evidence.vulnerability_evidence_bundle, "vulnerability scan attestation"
            )
            content_type, statement_bytes = verifier.verify_dsse(vulnerability_bundle, policy)
            _verify_vulnerability_statement(
                content_type=content_type,
                statement_bytes=statement_bytes,
                evidence=evidence,
            )
    except SupplyChainVerificationRejected:
        raise
    except (InvalidBundle, VerificationError, ValueError, TypeError) as exc:
        raise SupplyChainVerificationRejected("signed build evidence failed verification") from exc
    except Exception as exc:
        raise SupplyChainVerificationUnavailable(
            "Sigstore trust verification is temporarily unavailable"
        ) from exc
