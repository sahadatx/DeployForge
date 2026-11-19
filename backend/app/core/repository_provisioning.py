"""Durable GitHub repository generation for configured Golden Path templates."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.integrations.github_repositories import (
    GitHubRepositoryClient,
    GitHubRepositoryConfiguration,
    GitHubRepositoryError,
)
from app.models import GitHubRepositoryProvisioning, Service

REPOSITORY_PROVISIONING_LEASE_SECONDS = 90


def _repository_client() -> GitHubRepositoryClient | None:
    if not settings.github_repository_token:
        return None
    token = settings.github_repository_token.get_secret_value().strip()
    if not token:
        return None
    return GitHubRepositoryClient(
        GitHubRepositoryConfiguration(token=token, api_url=settings.github_api_url)
    )


def _expected_full_name(job: GitHubRepositoryProvisioning) -> str:
    return f"{job.repository_owner}/{job.repository_name}".casefold()


def _expected_template(job: GitHubRepositoryProvisioning) -> str:
    return f"{job.template_owner}/{job.template_name}".casefold()


def _validate_generated_repository(
    repository: dict, job: GitHubRepositoryProvisioning
) -> tuple[str, str | None, str | None]:
    full_name = str(repository.get("full_name") or "")
    template_repository = repository.get("template_repository")
    template_full_name = (
        str(template_repository.get("full_name") or "")
        if isinstance(template_repository, dict)
        else ""
    )
    if full_name.casefold() != _expected_full_name(job):
        raise GitHubRepositoryError(
            "github_repository_identity_mismatch",
            "GitHub returned a repository with an unexpected owner or name.",
        )
    if template_full_name.casefold() != _expected_template(job):
        raise GitHubRepositoryError(
            "github_repository_template_mismatch",
            "The existing repository was not generated from the configured Golden Path template.",
        )
    if bool(repository.get("private")) != job.is_private:
        raise GitHubRepositoryError(
            "github_repository_visibility_mismatch",
            "GitHub created the repository with an unexpected visibility.",
        )
    external_id = repository.get("id")
    external_id = str(external_id) if isinstance(external_id, (str, int)) else None
    default_branch = repository.get("default_branch")
    default_branch = str(default_branch)[:255] if default_branch else None
    return full_name, external_id, default_branch


def _audit_job(
    db: Session,
    job: GitHubRepositoryProvisioning,
    *,
    action: str,
    details: dict,
) -> None:
    record_audit(
        db,
        actor=None,
        action=action,
        resource_type="github_repository_provisioning",
        resource_id=job.id,
        workspace_id=job.workspace_id,
        details={
            "service_id": str(job.service_id),
            "golden_path_slug": job.golden_path_slug,
            "repository": f"{job.repository_owner}/{job.repository_name}",
            **details,
        },
    )


def process_next_repository_provisioning(db: Session, worker_name: str) -> bool:
    now = datetime.now(UTC)
    job = db.scalar(
        select(GitHubRepositoryProvisioning)
        .where(
            or_(
                (GitHubRepositoryProvisioning.status == "queued")
                & (GitHubRepositoryProvisioning.available_at <= now),
                (GitHubRepositoryProvisioning.status == "dispatching")
                & (GitHubRepositoryProvisioning.lease_expires_at <= now),
            )
        )
        .order_by(
            GitHubRepositoryProvisioning.available_at,
            GitHubRepositoryProvisioning.created_at,
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return False
    job.status = "dispatching"
    job.attempt_count += 1
    job.lease_owner = worker_name
    job.lease_expires_at = now + timedelta(seconds=REPOSITORY_PROVISIONING_LEASE_SECONDS)
    job.started_at = job.started_at or now
    job.updated_at = now
    _audit_job(
        db,
        job,
        action="catalog.repository_provisioning.dispatching",
        details={"attempt": job.attempt_count},
    )
    db.commit()
    db.refresh(job)

    client = _repository_client()
    service = db.get(Service, job.service_id)

    def block(code: str, message: str) -> None:
        job.status = "blocked"
        job.failure_code = code
        job.failure_message = message[:2000]
        job.finished_at = datetime.now(UTC)
        job.lease_owner = None
        job.lease_expires_at = None
        job.updated_at = datetime.now(UTC)
        _audit_job(
            db,
            job,
            action="catalog.repository_provisioning.blocked",
            details={"failure_code": code, "attempt": job.attempt_count},
        )
        db.commit()

    def succeed(repository: dict) -> None:
        _full_name, external_id, default_branch = _validate_generated_repository(repository, job)
        callback_secret = settings.build_callback_secret
        callback_url = settings.build_callback_public_url
        if not callback_secret or not callback_url:
            raise GitHubRepositoryError(
                "github_repository_callback_not_configured",
                "The worker requires the build callback secret and public URL to bootstrap CI.",
            )
        callback_allowed_host = urlsplit(callback_url).hostname
        if not callback_allowed_host:
            raise GitHubRepositoryError(
                "github_repository_callback_not_configured",
                "The callback URL has no valid host for the repository allowlist.",
            )
        client.configure_actions_bootstrap(
            job.repository_owner,
            job.repository_name,
            callback_secret=callback_secret.get_secret_value(),
            callback_allowed_host=callback_allowed_host,
        )
        job.status = "succeeded"
        job.external_repository_id = external_id
        job.repository_url = (
            f"{settings.github_server_url.rstrip('/')}/{job.repository_owner}/{job.repository_name}"
        )
        job.default_branch = default_branch
        job.failure_code = None
        job.failure_message = None
        job.finished_at = datetime.now(UTC)
        job.lease_owner = None
        job.lease_expires_at = None
        job.updated_at = datetime.now(UTC)
        if service is not None:
            service.repository_url = job.repository_url
            service.updated_at = datetime.now(UTC)
        _audit_job(
            db,
            job,
            action="catalog.repository_provisioning.succeeded",
            details={"default_branch": default_branch, "external_repository_id": external_id},
        )
        db.commit()

    if client is None or service is None:
        block(
            "github_repository_provisioning_unavailable",
            "The worker is missing its repository provisioning credential or service record.",
        )
        return True

    try:
        existing = client.get_repository(job.repository_owner, job.repository_name)
        if existing is not None:
            succeed(existing)
            return True
        repository = client.generate_from_template(
            template_owner=job.template_owner,
            template_name=job.template_name,
            owner=job.repository_owner,
            name=job.repository_name,
            description=service.description
            or f"{service.name} service from {job.golden_path_slug}",
            private=job.is_private,
        )
        succeed(repository)
    except GitHubRepositoryError as exc:
        if exc.code == "github_repository_generation_conflict":
            try:
                existing = client.get_repository(job.repository_owner, job.repository_name)
                if existing is not None:
                    succeed(existing)
                    return True
            except GitHubRepositoryError as lookup_error:
                exc = lookup_error
        if exc.retryable and job.attempt_count < job.max_attempts:
            job.status = "queued"
            job.available_at = datetime.now(UTC) + timedelta(
                seconds=min(15 * (2 ** (job.attempt_count - 1)), 300)
            )
            job.failure_code = exc.code
            job.failure_message = str(exc)[:2000]
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = datetime.now(UTC)
            _audit_job(
                db,
                job,
                action="catalog.repository_provisioning.retry_scheduled",
                details={"failure_code": exc.code, "attempt": job.attempt_count},
            )
            db.commit()
            return True
        block(exc.code, str(exc))
    return True
