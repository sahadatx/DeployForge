"""Seed a connected, synthetic workspace for local product walkthroughs.

Run with ``python -m app.demo_data seed --email you@company.com`` from the API
container. The command only runs when local development authentication is enabled.
"""

import argparse
import hashlib
import secrets
import sys
from datetime import UTC, datetime, timedelta

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalog_seed import seed_builtin_golden_paths
from app.core.audit import record_audit
from app.core.config import settings
from app.core.observability import evaluate_slo
from app.core.policies import evaluate_policy_set
from app.db.session import SessionLocal
from app.models import (
    AuditEvent,
    BuildArtifact,
    BuildRun,
    BuildRunEvent,
    Deployment,
    DeploymentEvent,
    Environment,
    GitOpsRun,
    GitOpsRunEvent,
    GitOpsTarget,
    GoldenPath,
    InfrastructureRun,
    InfrastructureRunEvent,
    InfrastructureTarget,
    Policy,
    PolicyEvaluation,
    PolicyException,
    Service,
    ServiceLevelObjective,
    SloAlert,
    SloEvaluation,
    ServiceTeam,
    TelemetrySample,
    TelemetrySource,
    User,
    Workspace,
    WorkspaceMembership,
)

SEED_SLUG = "northstar-commerce-demo"
SEED_MARKER = "[deployforge-demo-seed:v1]"
DEMO_NAME = "Northstar Commerce Demo"
DEMO_SOURCE = "Northstar Commerce Demo"
DEMO_SERVICE_TARGET = 110
DEMO_RELEASE_TARGET = 120
DEMO_INFRASTRUCTURE_RUN_TARGET = 100
DEMO_POLICY_EXCEPTION_TARGET = 100
DEMO_MEMBER_TARGET = 50
DEMO_MEMBER_ISSUER = "https://identity.northstar.example.invalid/oidc"
LOCAL_ISSUER = "urn:deployforge:development"

ENVIRONMENT_SPECS = (
    {
        "slug": "development",
        "name": "Development",
        "environment_type": "development",
        "cluster_name": "gke-northstar-dev",
        "namespace": "commerce-dev",
        "protected": False,
        "allow_destroy": True,
        "quota_cpu_millis": 16_000,
        "quota_memory_mib": 32_768,
        "quota_pods": 120,
        "quota_persistent_volume_claims": 30,
        "quota_services": 100,
    },
    {
        "slug": "staging",
        "name": "Staging",
        "environment_type": "staging",
        "cluster_name": "gke-northstar-staging",
        "namespace": "commerce-staging",
        "protected": False,
        "allow_destroy": False,
        "quota_cpu_millis": 24_000,
        "quota_memory_mib": 49_152,
        "quota_pods": 180,
        "quota_persistent_volume_claims": 40,
        "quota_services": 140,
    },
    {
        "slug": "production",
        "name": "Production",
        "environment_type": "production",
        "cluster_name": "gke-northstar-prod",
        "namespace": "commerce-prod",
        "protected": True,
        "allow_destroy": False,
        "quota_cpu_millis": 48_000,
        "quota_memory_mib": 98_304,
        "quota_pods": 360,
        "quota_persistent_volume_claims": 90,
        "quota_services": 260,
    },
)

TEAM_SPECS = (
    ("platform-engineering", "Platform Engineering", "Runtime, delivery and infrastructure"),
    ("payments", "Payments", "Checkout, ledger and settlement services"),
    ("customer-experience", "Customer Experience", "Storefront, catalog and messaging"),
)

SERVICE_SPECS = (
    {
        "slug": "checkout-api",
        "name": "Checkout API",
        "description": "Customer checkout orchestration and payment authorization.",
        "system": "Commerce Platform",
        "team": "payments",
        "path": "api-service",
        "lifecycle": "production",
        "criticality": "tier_1",
        "tags": ["api", "payments", "customer-facing"],
    },
    {
        "slug": "payment-ledger",
        "name": "Payment Ledger",
        "description": "Durable ledger entries and settlement reconciliation.",
        "system": "Commerce Platform",
        "team": "payments",
        "path": "api-service",
        "lifecycle": "production",
        "criticality": "tier_0",
        "tags": ["api", "payments", "financial-records"],
    },
    {
        "slug": "orders-worker",
        "name": "Orders Worker",
        "description": "Asynchronous order confirmation, fulfillment and retry handling.",
        "system": "Commerce Platform",
        "team": "platform-engineering",
        "path": "background-worker",
        "lifecycle": "production",
        "criticality": "tier_1",
        "tags": ["worker", "orders", "queue-consumer"],
    },
    {
        "slug": "catalog-api",
        "name": "Catalog API",
        "description": "Product availability, pricing and catalog search endpoints.",
        "system": "Customer Experience",
        "team": "customer-experience",
        "path": "api-service",
        "lifecycle": "production",
        "criticality": "tier_1",
        "tags": ["api", "catalog", "customer-facing"],
    },
    {
        "slug": "customer-portal",
        "name": "Customer Portal",
        "description": "Responsive storefront experience for registered customers.",
        "system": "Customer Experience",
        "team": "customer-experience",
        "path": "web-application",
        "lifecycle": "production",
        "criticality": "tier_2",
        "tags": ["web", "storefront", "customer-facing"],
    },
    {
        "slug": "notification-worker",
        "name": "Notification Worker",
        "description": "Transactional email and order notification delivery.",
        "system": "Customer Experience",
        "team": "customer-experience",
        "path": "background-worker",
        "lifecycle": "production",
        "criticality": "tier_2",
        "tags": ["worker", "notifications", "queue-consumer"],
    },
    {
        "slug": "search-indexer",
        "name": "Search Indexer",
        "description": "Incremental product search indexing and backfill jobs.",
        "system": "Customer Experience",
        "team": "platform-engineering",
        "path": "background-worker",
        "lifecycle": "experimental",
        "criticality": "tier_2",
        "tags": ["worker", "search", "batch"],
    },
)

SERVICE_DOMAINS = (
    "accounts", "identity", "authorization", "billing", "invoices", "subscriptions",
    "checkout", "payments", "ledger", "orders", "inventory", "catalog", "search",
    "recommendations", "pricing", "promotions", "cart", "fulfillment", "shipping",
    "returns", "notifications", "messaging", "analytics", "reporting", "webhooks",
    "fraud", "tax", "customers", "merchants", "partners", "support", "reviews",
)


def _extended_service_specs() -> tuple[dict, ...]:
    """Build a believable catalog of production services for the demo workspace."""
    existing = {spec["slug"] for spec in SERVICE_SPECS}
    rows = []
    roles = (
        ("api", "API", "api-service"),
        ("worker", "Worker", "background-worker"),
        ("web", "Web", "web-application"),
        ("indexer", "Indexer", "background-worker"),
    )
    payment_domains = {"billing", "invoices", "subscriptions", "checkout", "payments", "ledger", "fraud", "tax"}
    experience_domains = {
        "catalog", "search", "recommendations", "pricing", "promotions", "cart", "shipping",
        "returns", "notifications", "messaging", "customers", "support", "reviews",
    }
    for domain in SERVICE_DOMAINS:
        for role, role_label, path in roles:
            slug = f"{domain}-{role}"
            if slug in existing:
                continue
            if domain in payment_domains:
                team = "payments"
                system = "Commerce and Payments"
            elif domain in experience_domains:
                team = "customer-experience"
                system = "Customer Experience"
            else:
                team = "platform-engineering"
                system = "Commerce Platform"
            noun = domain.replace("-", " ").title()
            rows.append(
                {
                    "slug": slug,
                    "name": f"{noun} {role_label}",
                    "description": (
                        f"{noun} {role_label.lower()} supporting Northstar Commerce operations "
                        "with monitored releases and team ownership."
                    ),
                    "system": system,
                    "team": team,
                    "path": path,
                    "lifecycle": "experimental" if len(rows) % 11 == 0 else "production",
                    "criticality": ("tier_0", "tier_1", "tier_2")[len(rows) % 3],
                    "tags": [role, domain, "customer-facing" if team == "customer-experience" else "internal"],
                }
            )
            if len(rows) >= DEMO_SERVICE_TARGET - len(SERVICE_SPECS):
                return tuple(rows)
    return tuple(rows)


ALL_SERVICE_SPECS = SERVICE_SPECS + _extended_service_specs()

# Service, environment, outcome, age in days, release version, source branch.
DEPLOYMENT_SPECS = (
    ("checkout-api", "production", "succeeded", 0.2, "2.18.4", "main"),
    ("payment-ledger", "production", "failed", 0.5, "4.7.2", "main"),
    ("orders-worker", "staging", "succeeded", 1, "3.12.0-rc.2", "release/3.12"),
    ("catalog-api", "production", "succeeded", 1.4, "6.3.1", "main"),
    ("customer-portal", "production", "failed", 2.2, "8.4.0", "main"),
    ("notification-worker", "staging", "succeeded", 3, "1.9.3-rc.1", "release/1.9"),
    ("search-indexer", "development", "succeeded", 3.5, "0.8.1-dev.14", "feature/index-v2"),
    ("checkout-api", "staging", "succeeded", 5, "2.18.3-rc.3", "release/2.18"),
    ("payment-ledger", "staging", "succeeded", 6, "4.7.1-rc.2", "release/4.7"),
    ("orders-worker", "production", "rolled_back", 7, "3.11.8", "main"),
    ("orders-worker", "production", "succeeded", 14, "3.11.6", "main"),
    ("catalog-api", "staging", "succeeded", 8, "6.3.0-rc.1", "release/6.3"),
    ("customer-portal", "staging", "succeeded", 9, "8.3.4-rc.2", "release/8.4"),
    ("notification-worker", "production", "succeeded", 10, "1.9.2", "main"),
    ("search-indexer", "staging", "failed", 11, "0.8.0-rc.4", "release/0.8"),
    ("checkout-api", "development", "succeeded", 12, "2.18.2-dev.6", "feature/express-checkout"),
    ("payment-ledger", "production", "succeeded", 13, "4.7.0", "main"),
    ("orders-worker", "development", "succeeded", 15, "3.12.0-dev.8", "feature/batch-ack"),
    ("catalog-api", "development", "succeeded", 16, "6.2.7-dev.3", "feature/catalog-v3"),
    ("customer-portal", "development", "succeeded", 18, "8.3.3-dev.12", "feature/account-home"),
    (
        "notification-worker",
        "development",
        "canceled",
        20,
        "1.9.1-dev.2",
        "feature/template-refresh",
    ),
    ("search-indexer", "production", "rejected", 22, "0.7.9", "main"),
    ("checkout-api", "production", "succeeded", 24, "2.18.1", "main"),
    ("payment-ledger", "development", "succeeded", 26, "4.6.8-dev.5", "feature/settlement-v2"),
    ("orders-worker", "staging", "succeeded", 28, "3.11.7-rc.1", "release/3.12"),
    ("catalog-api", "production", "succeeded", 35, "6.2.4", "main"),
    ("catalog-api", "production", "rolled_back", 30, "6.2.5", "main"),
    ("customer-portal", "production", "succeeded", 32, "8.3.1", "main"),
    ("notification-worker", "production", "failed", 35, "1.8.9", "main"),
    ("search-indexer", "development", "succeeded", 37, "0.7.8-dev.11", "feature/index-v2"),
)


def _all_deployment_specs() -> tuple[tuple, ...]:
    """Return a six-week release history with more than 100 realistic records."""
    specs = list(DEPLOYMENT_SPECS)
    extended_services = [spec for spec in ALL_SERVICE_SPECS if spec not in SERVICE_SPECS]
    statuses = (
        "succeeded", "succeeded", "succeeded", "succeeded", "succeeded", "succeeded",
        "succeeded", "succeeded", "failed", "succeeded", "rolled_back", "succeeded",
        "rejected", "canceled", "succeeded",
    )
    for index in range(len(specs), DEMO_RELEASE_TARGET):
        release_index = index - len(DEPLOYMENT_SPECS)
        service = extended_services[release_index % len(extended_services)]
        environment = ENVIRONMENT_SPECS[index % len(ENVIRONMENT_SPECS)]["slug"]
        status = statuses[release_index % len(statuses)]
        age_days = 0.6 + release_index * 0.55
        version = f"{(release_index % 6) + 1}.{(release_index // 6) % 12}.{release_index % 9}"
        source_ref = (
            "main"
            if environment == "production"
            else f"release/{version.split('.')[0]}.{version.split('.')[1]}"
            if environment == "staging"
            else f"feature/{service['slug']}"
        )
        specs.append(
            (service["slug"], environment, status, age_days, version, source_ref)
        )
    return tuple(specs)

POLICY_SPECS = (
    {
        "slug": "production-release-guardrails",
        "name": "Production release guardrails",
        "description": "Review protected releases and reject critical supply-chain findings.",
        "actions": ["deployment"],
        "rules": [
            {
                "id": "protected-environment-review",
                "name": "Protected environment review",
                "description": "Protected releases require an independent approval.",
                "field": "environment.protected",
                "operator": "equals",
                "expected": True,
                "effect": "require_approval",
            },
            {
                "id": "block-critical-vulnerabilities",
                "name": "Block critical vulnerabilities",
                "description": "A critical vulnerability blocks a production artifact.",
                "field": "artifact.critical_vulnerabilities",
                "operator": "greater_than",
                "expected": 0,
                "effect": "deny",
            },
        ],
    },
    {
        "slug": "infrastructure-change-guardrails",
        "name": "Infrastructure change guardrails",
        "description": "Protect production plans and prevent destructive changes.",
        "actions": ["infrastructure"],
        "rules": [
            {
                "id": "protected-infrastructure-review",
                "name": "Protected infrastructure review",
                "description": "Protected environment plans require a second review.",
                "field": "environment.protected",
                "operator": "equals",
                "expected": True,
                "effect": "require_approval",
            },
            {
                "id": "prevent-resource-destruction",
                "name": "Prevent resource destruction",
                "description": "Plans that destroy resources are blocked for review.",
                "field": "infrastructure.resource_destroys",
                "operator": "greater_than",
                "expected": 0,
                "effect": "deny",
            },
        ],
    },
)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _demo_details(**values: object) -> dict:
    return {"demo_dataset": True, "source": DEMO_SOURCE, **values}


def _insert_event(db: Session, model, **values) -> None:
    event_details = values.pop("details", {})
    db.add(model(details=_demo_details(**event_details), **values))


def _align_audit_event(db: Session, *, action: str, resource_id: str, created_at: datetime) -> None:
    for event in tuple(db.new):
        if (
            isinstance(event, AuditEvent)
            and event.action == action
            and event.resource_id == str(resource_id)
        ):
            event.created_at = created_at


def _require_local_seed_mode() -> None:
    if (
        settings.app_env not in {"development", "dev", "local"}
        or not settings.dev_auth_enabled
        or settings.runtime_role != "api"
    ):
        raise ValueError(
            "Demo data can only be seeded by the local API with development auth enabled"
        )


def _find_or_create_workspace(db: Session, user: User) -> tuple[Workspace, bool]:
    workspace = db.scalar(select(Workspace).where(Workspace.slug == SEED_SLUG))
    if workspace is not None:
        if not workspace.description or SEED_MARKER not in workspace.description:
            raise ValueError(f"Workspace slug '{SEED_SLUG}' already belongs to another workspace")
        _ensure_demo_operator_membership(db, workspace, user)
        db.commit()
        return workspace, False

    description = (
        "Synthetic services and operational history for a local walkthrough. "
        "Provider jobs are not connected. "
        f"{SEED_MARKER}"
    )
    workspace = Workspace(slug=SEED_SLUG, name=DEMO_NAME, description=description)
    db.add(workspace)
    db.flush()
    _ensure_demo_operator_membership(db, workspace, user)
    db.flush()
    return workspace, True


def _ensure_demo_operator_membership(db: Session, workspace: Workspace, user: User) -> None:
    """Give the local demo operator write access only within the synthetic workspace."""
    membership = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace.id,
            WorkspaceMembership.user_id == user.id,
        )
    )
    if membership is None:
        membership = WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role="platform_engineer" if user.issuer == LOCAL_ISSUER else "auditor",
            granted_by=None,
        )
        db.add(membership)
        db.flush()
        record_audit(
            db,
            actor=user,
            action="workspace.membership.granted",
            resource_type="workspace_membership",
            resource_id=membership.id,
            workspace_id=workspace.id,
            details={"target_user_id": str(user.id), "role": membership.role, "source": "local_demo_seed"},
        )
        return

    if (
        user.issuer == LOCAL_ISSUER and membership.role in {"auditor", "service_owner"}
    ):
        previous_role = membership.role
        membership.role = "platform_engineer"
        record_audit(
            db,
            actor=user,
            action="workspace.membership.role_changed",
            resource_type="workspace_membership",
            resource_id=membership.id,
            workspace_id=workspace.id,
            details={
                "target_user_id": str(user.id),
                "from_role": previous_role,
                "to_role": membership.role,
                "source": "local_demo_seed",
            },
        )


def _seed_environment_and_services(db: Session, workspace: Workspace, now: datetime):
    environments = {
        item.slug: item
        for item in db.scalars(
            select(Environment).where(Environment.workspace_id == workspace.id)
        ).all()
    }
    new_environments = []
    for spec in ENVIRONMENT_SPECS:
        if spec["slug"] not in environments:
            environment = Environment(
                workspace_id=workspace.id,
                created_at=now - timedelta(days=50),
                **spec,
            )
            environments[spec["slug"]] = environment
            new_environments.append(environment)
    db.add_all(new_environments)
    environments = {spec["slug"]: environments[spec["slug"]] for spec in ENVIRONMENT_SPECS}

    teams = {
        item.slug: item
        for item in db.scalars(
            select(ServiceTeam).where(ServiceTeam.workspace_id == workspace.id)
        ).all()
    }
    new_teams = []
    for slug, name, description in TEAM_SPECS:
        if slug not in teams:
            team = ServiceTeam(
                workspace_id=workspace.id,
                slug=slug,
                name=name,
                description=description,
                created_at=now - timedelta(days=52),
                updated_at=now - timedelta(days=2),
            )
            teams[slug] = team
            new_teams.append(team)
    db.add_all(new_teams)
    db.flush()

    paths = {path.slug: path for path in db.scalars(select(GoldenPath)).all()}
    services = {
        item.slug: item
        for item in db.scalars(select(Service).where(Service.workspace_id == workspace.id)).all()
    }
    new_services = []
    for index, spec in enumerate(ALL_SERVICE_SPECS):
        if spec["slug"] in services:
            continue
        path = paths[spec["path"]]
        slug = spec["slug"]
        service = Service(
            workspace_id=workspace.id,
            slug=slug,
            name=spec["name"],
            description=spec["description"],
            system=spec["system"],
            owner_team_id=teams[spec["team"]].id,
            repository_url=f"https://github.com/northstar-commerce-demo/{slug}",
            documentation_url=f"https://docs.northstar.example/services/{slug}",
            lifecycle=spec["lifecycle"],
            criticality=spec["criticality"],
            tags=spec["tags"],
            metadata_={"demo_dataset": True, "runtime": "container", "region": "us-east-1"},
            golden_path_id=path.id,
            golden_path_snapshot={"slug": path.slug, "name": path.name, "version": path.version},
            created_by=None,
            created_at=now - timedelta(days=45 - index),
            updated_at=now - timedelta(days=index % 5),
        )
        services[slug] = service
        new_services.append(service)
    db.add_all(new_services)
    db.flush()
    return environments, teams, services


def _policy_context(service: Service, environment: Environment) -> dict:
    return {
        "service": {
            "slug": service.slug,
            "lifecycle": service.lifecycle,
            "criticality": service.criticality,
            "tags": service.tags,
            "repository_url": service.repository_url,
        },
        "environment": {
            "slug": environment.slug,
            "type": environment.environment_type,
            "protected": environment.protected,
        },
    }


def _deployment_events(
    db: Session,
    deployment: Deployment,
    *,
    status: str,
    policy_evaluation_id: str,
    when: datetime,
) -> None:
    common = {
        "deployment_id": deployment.id,
        "workspace_id": deployment.workspace_id,
        "actor_type": "system",
        "actor_id": "demo-seed",
        "details": _demo_details(policy_evaluation_id=policy_evaluation_id),
    }
    if status == "canceled":
        states = [
            (None, "queued", "Release request recorded"),
            ("queued", "canceled", "Release canceled before execution"),
        ]
    elif status == "rejected":
        states = [
            (None, "queued", "Release request recorded"),
            ("queued", "validating", "Release passed to catalog validation"),
            ("validating", "policy_check", "Service and environment references validated"),
            ("policy_check", "awaiting_approval", "Protected release is awaiting review"),
            ("awaiting_approval", "rejected", "Sample release rejected during review"),
        ]
    elif status == "rolled_back":
        states = [
            (None, "queued", "Release request recorded"),
            ("queued", "validating", "Release passed to catalog validation"),
            ("validating", "policy_check", "Service and environment references validated"),
            ("policy_check", "awaiting_approval", "Protected release is awaiting review"),
            ("awaiting_approval", "approved", "Sample release review completed"),
            ("approved", "building", "Build workflow started"),
            ("building", "deploying", "Artifact promoted to the target environment"),
            ("deploying", "verifying", "Release health checks started"),
            ("verifying", "succeeded", "Release health checks passed"),
            ("succeeded", "rollback_pending", "A later health signal requested rollback"),
            ("rollback_pending", "rolled_back", "Previous healthy revision restored"),
        ]
    elif status == "failed":
        states = [
            (None, "queued", "Release request recorded"),
            ("queued", "validating", "Release passed to catalog validation"),
            ("validating", "policy_check", "Service and environment references validated"),
            ("policy_check", "awaiting_approval", "Release review requested"),
            ("awaiting_approval", "approved", "Sample release review completed"),
            ("approved", "building", "Build workflow started"),
            ("building", "failed", "Build or release health check failed"),
        ]
    else:
        states = [
            (None, "queued", "Release request recorded"),
            ("queued", "validating", "Release passed to catalog validation"),
            ("validating", "policy_check", "Service and environment references validated"),
            ("policy_check", "awaiting_approval", "Release review requested"),
            ("awaiting_approval", "approved", "Sample release review completed"),
            ("approved", "building", "Build workflow started"),
            ("building", "deploying", "Artifact promoted to the target environment"),
            ("deploying", "verifying", "Release health checks started"),
            ("verifying", "succeeded", "Release health checks passed"),
        ]
    for sequence, (from_status, to_status, message) in enumerate(states, start=1):
        event_type = "policy.evaluated" if to_status == "awaiting_approval" else "state.changed"
        details = dict(common["details"])
        _insert_event(
            db,
            DeploymentEvent,
            deployment_id=deployment.id,
            workspace_id=deployment.workspace_id,
            sequence=sequence,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            actor_type=common["actor_type"],
            actor_id=common["actor_id"],
            message=message,
            created_at=when + timedelta(minutes=sequence * 2),
            **{"details": details},
        )


def _create_deployments(
    db: Session,
    workspace: Workspace,
    environments: dict[str, Environment],
    services: dict[str, Service],
    now: datetime,
) -> list[Deployment]:
    existing = {
        item.idempotency_key: item
        for item in db.scalars(
            select(Deployment).where(Deployment.workspace_id == workspace.id)
        ).all()
        if item.idempotency_key
    }
    new_deployments = []
    for index, (service_slug, env_slug, status, age_days, version, source_ref) in enumerate(
        _all_deployment_specs()
    ):
        idempotency_key = f"northstar-demo-release-{index + 1:03d}"
        if idempotency_key in existing:
            continue
        created_at = now - timedelta(days=age_days)
        failed = status == "failed"
        finished_minutes = 4 if status == "canceled" else 30 if status == "rolled_back" else 18
        deployment = Deployment(
            workspace_id=workspace.id,
            service_id=services[service_slug].id,
            environment_id=environments[env_slug].id,
            version=version,
            source_ref=source_ref,
            status=status,
            requested_by="release-bot@northstar.example",
            idempotency_key=idempotency_key,
            attempt_count=2 if failed and index % 2 else 1,
            max_attempts=3,
            available_at=created_at,
            started_at=created_at + timedelta(minutes=1) if status != "canceled" else None,
            finished_at=created_at + timedelta(minutes=finished_minutes),
            failure_code="release_health_check_failed" if failed else None,
            failure_message=(
                "Post-deploy readiness checks did not reach the required threshold."
                if failed
                else None
            ),
            created_at=created_at,
            updated_at=created_at + timedelta(minutes=finished_minutes),
        )
        new_deployments.append(deployment)
    db.add_all(new_deployments)
    db.flush()

    for deployment in new_deployments:
        service = db.get(Service, deployment.service_id)
        environment = db.get(Environment, deployment.environment_id)
        evaluation = evaluate_policy_set(
            db,
            workspace_id=workspace.id,
            action="deployment",
            context=_policy_context(service, environment)
            | {"deployment": {"version": deployment.version, "source_ref": deployment.source_ref}},
            subject_type="deployment",
            subject_id=deployment.id,
            service_id=service.id,
            environment_id=environment.id,
            defer_artifact_rules=True,
        )
        evaluation.created_at = deployment.created_at
        _align_audit_event(
            db,
            action="policy.evaluated",
            resource_id=str(deployment.id),
            created_at=deployment.created_at,
        )
        _deployment_events(
            db,
            deployment,
            status=deployment.status,
            policy_evaluation_id=str(evaluation.id),
            when=deployment.created_at,
        )
    db.flush()
    return list(
        db.scalars(
            select(Deployment)
            .where(Deployment.workspace_id == workspace.id)
            .order_by(Deployment.created_at.desc(), Deployment.id)
        ).all()
    )


def _create_builds_and_artifacts(
    db: Session,
    workspace: Workspace,
    services: dict[str, Service],
    deployments: list[Deployment],
    now: datetime,
):
    builds = {}
    artifacts = {}
    deployment_ids = [deployment.id for deployment in deployments]
    existing_builds = {
        build.deployment_id: build
        for build in db.scalars(
            select(BuildRun).where(BuildRun.deployment_id.in_(deployment_ids))
        ).all()
    } if deployment_ids else {}
    for index, deployment in enumerate(deployments):
        existing_build = existing_builds.get(deployment.id)
        if existing_build is not None:
            builds[deployment.id] = existing_build
            artifact = db.scalar(
                select(BuildArtifact).where(BuildArtifact.build_run_id == existing_build.id)
            )
            if artifact is not None:
                artifacts[deployment.id] = artifact
            continue
        if deployment.status in {"rejected", "canceled"}:
            continue
        service = db.get(Service, deployment.service_id)
        build_status = "canceled" if deployment.status == "canceled" else "succeeded"
        if deployment.status == "failed" and index % 29 in {1, 13}:
            build_status = "failed"
        finished_at = deployment.finished_at or now
        commit_sha = hashlib.sha1(
            f"northstar-demo:{service.slug}:{deployment.version}".encode()
        ).hexdigest()
        build = BuildRun(
            workspace_id=workspace.id,
            deployment_id=deployment.id,
            service_id=service.id,
            requested_by=None,
            attempt_number=1,
            provider="github_actions",
            repository_url=service.repository_url,
            workflow_path=".github/workflows/deployforge-build.yml",
            source_ref=deployment.source_ref or "main",
            commit_sha=commit_sha,
            gitops_revision=commit_sha,
            status=build_status,
            attempt_count=1,
            max_attempts=3,
            available_at=deployment.created_at,
            started_at=deployment.started_at,
            finished_at=finished_at,
            failure_code="workflow_failed" if build_status == "failed" else None,
            failure_message=(
                "The sample workflow failed during image scanning; "
                "no release artifact was promoted."
                if build_status == "failed"
                else None
            ),
            created_at=deployment.created_at,
            updated_at=finished_at,
        )
        db.add(build)
        db.flush()
        builds[deployment.id] = build
        build_states = [(None, "queued"), ("queued", "running"), ("running", build_status)]
        for sequence, (from_status, to_status) in enumerate(build_states, start=1):
            _insert_event(
                db,
                BuildRunEvent,
                build_run_id=build.id,
                workspace_id=workspace.id,
                sequence=sequence,
                delivery_id=None,
                event_type="workflow.completed"
                if to_status == build_status
                else "workflow.progress",
                from_status=from_status,
                to_status=to_status,
                actor_type="system",
                actor_id="demo-seed",
                message=(
                    "Sample build completed successfully."
                    if to_status == "succeeded"
                    else "Sample build finished with a recorded outcome."
                    if to_status == build_status
                    else "Sample build workflow advanced."
                ),
                created_at=deployment.created_at + timedelta(minutes=sequence * 3),
            )
        if build_status == "succeeded":
            digest = _digest(f"image:{service.slug}:{deployment.version}")
            artifact = BuildArtifact(
                workspace_id=workspace.id,
                build_run_id=build.id,
                name=f"{service.slug}:{deployment.version}",
                artifact_type="container",
                uri=f"ghcr.io/northstar-commerce-demo/{service.slug}@{digest}",
                digest=digest,
                sbom_format="cyclonedx-json",
                sbom_uri=f"oci://northstar-demo-artifacts/{service.slug}/{deployment.version}/sbom.json",
                sbom_digest=_digest(f"sbom:{service.slug}:{deployment.version}"),
                vulnerability_summary={
                    "critical": 0,
                    "high": index % 3,
                    "medium": 3 + index % 5,
                    "low": 8 + index % 7,
                },
                signature_uri=f"oci://northstar-demo-artifacts/{service.slug}/{deployment.version}/signature.sig",
                signer_identity=(
                    "https://github.com/northstar-commerce-demo/.github/workflows/"
                    "deployforge-build.yml@refs/heads/main"
                ),
                oidc_issuer="https://token.actions.githubusercontent.com",
                provenance_uri=f"oci://northstar-demo-artifacts/{service.slug}/{deployment.version}/provenance.json",
                provenance_digest=_digest(f"provenance:{service.slug}:{deployment.version}"),
                evidence_status="workflow_reported",
                created_at=finished_at,
            )
            db.add(artifact)
            db.flush()
            artifacts[deployment.id] = artifact

    all_builds = db.scalars(
        select(BuildRun)
        .where(BuildRun.workspace_id == workspace.id)
        .order_by(BuildRun.created_at.desc(), BuildRun.id)
    ).all()
    for build in all_builds:
        builds.setdefault(build.deployment_id, build)
        artifact = db.scalar(
            select(BuildArtifact).where(BuildArtifact.build_run_id == build.id)
        )
        if artifact is not None:
            artifacts.setdefault(build.deployment_id, artifact)
    return builds, artifacts


def _create_gitops(
    db: Session,
    workspace: Workspace,
    environments: dict[str, Environment],
    services: dict[str, Service],
    deployments: list[Deployment],
    builds: dict,
    artifacts: dict,
    now: datetime,
):
    eligible = [
        deployment
        for deployment in deployments
        if deployment.status in {"succeeded", "failed", "rolled_back"}
        and deployment.id in builds
        and builds[deployment.id].status == "succeeded"
    ]
    target_specs = {}
    for deployment in eligible:
        service = db.get(Service, deployment.service_id)
        environment = db.get(Environment, deployment.environment_id)
        target_specs[(service.id, environment.id)] = (service, environment)
    target_keys = set(target_specs)
    existing_targets = {
        (target.service_id, target.environment_id): target
        for target in db.scalars(
            select(GitOpsTarget)
            .where(GitOpsTarget.workspace_id == workspace.id)
            .order_by(GitOpsTarget.created_at)
        ).all()
        if (target.service_id, target.environment_id) in target_keys
    }
    targets = dict(existing_targets)
    for (service_id, environment_id), (service, environment) in target_specs.items():
        if (service_id, environment_id) in targets:
            continue
        target = GitOpsTarget(
            workspace_id=workspace.id,
            service_id=service_id,
            environment_id=environment_id,
            application_name=f"{service.slug}-{environment.slug}",
            expected_project="commerce",
            expected_cluster_name=environment.cluster_name,
            artifact_name=service.slug,
            enabled=False,
            created_by=None,
            created_at=now - timedelta(days=42),
            updated_at=now - timedelta(days=1),
        )
        db.add(target)
        targets[(service_id, environment_id)] = target
    db.flush()

    existing_runs = {
        run.deployment_id: run
        for run in db.scalars(
            select(GitOpsRun).where(GitOpsRun.workspace_id == workspace.id)
        ).all()
    }
    runs = list(existing_runs.values())
    previous_success = {}
    for deployment in sorted(eligible, key=lambda item: _utc_datetime(item.created_at)):
        service = db.get(Service, deployment.service_id)
        environment = db.get(Environment, deployment.environment_id)
        target = targets[(service.id, environment.id)]
        if deployment.id in existing_runs:
            if deployment.status == "succeeded":
                previous_success[(service.id, environment.id)] = existing_runs[deployment.id]
            continue
        build = builds[deployment.id]
        artifact = artifacts.get(deployment.id)
        status = deployment.status
        revision = build.commit_sha
        previous = previous_success.get((service.id, environment.id))
        run = GitOpsRun(
            workspace_id=workspace.id,
            deployment_id=deployment.id,
            build_run_id=build.id,
            target_id=target.id,
            rollback_from_run_id=previous.id if status == "rolled_back" and previous else None,
            attempt_number=1,
            action_kind="rollback" if status == "rolled_back" else "deploy",
            application_name=target.application_name,
            desired_revision=revision,
            observed_revision=revision if status in {"succeeded", "rolled_back"} else None,
            artifact_uri=artifact.uri if artifact else None,
            artifact_digest=artifact.digest if artifact else None,
            status=status,
            sync_status="Synced" if status in {"succeeded", "rolled_back"} else "OutOfSync",
            health_status="Healthy" if status in {"succeeded", "rolled_back"} else "Degraded",
            operation_phase="Succeeded" if status in {"succeeded", "rolled_back"} else "Failed",
            destination_name=environment.cluster_name,
            destination_server=f"https://{environment.slug}.northstar.example.invalid",
            attempt_count=1,
            max_attempts=3,
            failure_code="sync_verification_failed" if status == "failed" else None,
            failure_message=(
                "The sample reconciliation did not become healthy before the rollout window closed."
                if status == "failed"
                else None
            ),
            started_at=deployment.started_at,
            finished_at=deployment.finished_at,
            created_at=deployment.created_at + timedelta(minutes=4),
            updated_at=deployment.finished_at or deployment.created_at,
        )
        db.add(run)
        db.flush()
        runs.append(run)
        if status == "succeeded":
            previous_success[(service.id, environment.id)] = run
        state_sequence = [
            (None, "queued"),
            ("queued", "dispatching"),
            ("dispatching", "syncing"),
            ("syncing", "verifying"),
            ("verifying", status),
        ]
        for sequence, (from_status, to_status) in enumerate(state_sequence, start=1):
            _insert_event(
                db,
                GitOpsRunEvent,
                gitops_run_id=run.id,
                workspace_id=workspace.id,
                sequence=sequence,
                event_type="reconciliation.completed"
                if to_status == status
                else "reconciliation.progress",
                from_status=from_status,
                to_status=to_status,
                actor_type="system",
                actor_id="demo-seed",
                message=(
                    "Sample application reconciliation completed."
                    if to_status == status
                    else "Sample application reconciliation advanced."
                ),
                created_at=run.created_at + timedelta(minutes=sequence * 2),
            )
    all_targets = db.scalars(
        select(GitOpsTarget)
        .where(GitOpsTarget.workspace_id == workspace.id)
        .order_by(GitOpsTarget.created_at, GitOpsTarget.id)
    ).all()
    all_runs = db.scalars(
        select(GitOpsRun)
        .where(GitOpsRun.workspace_id == workspace.id)
        .order_by(GitOpsRun.created_at.desc(), GitOpsRun.id)
    ).all()
    return all_targets, all_runs


def _create_infrastructure(
    db: Session,
    workspace: Workspace,
    environments: dict[str, Environment],
    now: datetime,
):
    targets = {
        item.environment_id: item
        for item in db.scalars(
            select(InfrastructureTarget).where(
                InfrastructureTarget.workspace_id == workspace.id
            )
        ).all()
    }
    for env_slug, environment in environments.items():
        if environment.id in targets:
            continue
        target = InfrastructureTarget(
            workspace_id=workspace.id,
            environment_id=environment.id,
            repository_url="https://github.com/northstar-commerce-demo/platform-infrastructure",
            source_ref="main",
            working_directory=f"environments/{env_slug}",
            tool="terraform",
            backend_workspace=f"northstar-{env_slug}",
            enabled=False,
            created_by=None,
            created_at=now - timedelta(days=48),
            updated_at=now - timedelta(days=1),
        )
        db.add(target)
        targets[environment.id] = target
    db.flush()

    status_cycle = (
        "succeeded",
        "succeeded",
        "drifted",
        "succeeded",
        "failed",
        "succeeded",
        "blocked",
        "succeeded",
        "canceled",
        "succeeded",
        "drifted",
        "failed",
        "succeeded",
        "succeeded",
        "blocked",
        "succeeded",
        "succeeded",
        "succeeded",
    )
    runs = []
    env_names = tuple(spec["slug"] for spec in ENVIRONMENT_SPECS)
    existing_digests = set(
        db.scalars(
            select(InfrastructureRun.plan_digest).where(
                InfrastructureRun.workspace_id == workspace.id
            )
        ).all()
    )
    for index in range(DEMO_INFRASTRUCTURE_RUN_TARGET):
        status = status_cycle[index % len(status_cycle)]
        env_slug = env_names[index % len(env_names)]
        environment = environments[env_slug]
        target = targets[environment.id]
        plan_digest = _digest(f"infra-plan:{env_slug}:{index}")
        if plan_digest in existing_digests:
            continue
        created_at = now - timedelta(days=1 + index * 0.95)
        run_kind = "drift_check" if index in {2, 10} else "plan"
        plan_has_changes = run_kind == "plan" and (index % 4 == 0 or status == "blocked")
        resource_additions = 2 if plan_has_changes else 0
        resource_changes = 1 if plan_has_changes else 0
        run = InfrastructureRun(
            workspace_id=workspace.id,
            environment_id=environment.id,
            target_id=target.id,
            requested_by=None,
            approved_by=None,
            attempt_number=1,
            attempt_count=1,
            max_attempts=3,
            run_kind=run_kind,
            active_operation=None,
            status=status,
            source_ref="main" if env_slug == "production" else "release/platform-2026.11",
            working_directory=target.working_directory,
            plan_digest=plan_digest,
            plan_artifact_uri=f"oci://northstar-demo-artifacts/infra/{env_slug}/plan-{index:03d}.json",
            plan_has_changes=plan_has_changes,
            resource_additions=resource_additions,
            resource_changes=resource_changes,
            resource_destroys=1 if status == "blocked" else 0,
            quota_usage={
                "cpu_millis": min(9_000 + index * 240, environment.quota_cpu_millis),
                "memory_mib": min(14_000 + index * 300, environment.quota_memory_mib),
                "pods": min(32 + index, environment.quota_pods),
                "persistent_volume_claims": min(
                    12 + index % 15, environment.quota_persistent_volume_claims
                ),
                "services": min(20 + index, environment.quota_services),
            },
            available_at=created_at,
            failure_code=(
                "provider_plan_failed"
                if status == "failed"
                else "policy_denied"
                if status == "blocked"
                else None
            ),
            failure_message=(
                "The sample provider could not refresh state from its remote backend."
                if status == "failed"
                else "The sample plan was blocked by an infrastructure guardrail."
                if status == "blocked"
                else None
            ),
            started_at=created_at + timedelta(minutes=2),
            finished_at=created_at + timedelta(minutes=11),
            created_at=created_at,
            updated_at=created_at + timedelta(minutes=11),
        )
        db.add(run)
        db.flush()
        runs.append(run)
        existing_digests.add(plan_digest)
        for sequence, (from_status, to_status, event_type, message) in enumerate(
            (
                (None, "queued", "requested", "Sample infrastructure run recorded"),
                (
                    "queued",
                    "dispatching",
                    "provider.dispatching",
                    "Sample provider workflow started",
                ),
                (
                    "dispatching",
                    "running",
                    "provider.started",
                    "Terraform plan or drift check started",
                ),
                ("running", status, "provider.completed", "Sample infrastructure run completed"),
            ),
            start=1,
        ):
            _insert_event(
                db,
                InfrastructureRunEvent,
                infrastructure_run_id=run.id,
                workspace_id=workspace.id,
                sequence=sequence,
                delivery_id=None,
                event_type=event_type,
                from_status=from_status,
                to_status=to_status,
                actor_type="system",
                actor_id="demo-seed",
                message=message,
                created_at=created_at + timedelta(minutes=sequence * 2),
            )
    all_runs = db.scalars(
        select(InfrastructureRun)
        .where(InfrastructureRun.workspace_id == workspace.id)
        .order_by(InfrastructureRun.created_at.desc(), InfrastructureRun.id)
    ).all()
    return {env_slug: targets[environment.id] for env_slug, environment in environments.items()}, all_runs


def _evaluate_infrastructure_policies(
    db: Session,
    workspace: Workspace,
    environments: dict[str, Environment],
    targets: dict[str, InfrastructureTarget],
    runs: list[InfrastructureRun],
) -> None:
    for run in runs:
        if run.run_kind != "plan" or db.scalar(
            select(PolicyEvaluation.id).where(
                PolicyEvaluation.subject_type == "infrastructure_run",
                PolicyEvaluation.subject_id == str(run.id),
            )
        ) is not None:
            continue
        environment = next(env for env in environments.values() if env.id == run.environment_id)
        target = targets[environment.slug]
        evaluation = evaluate_policy_set(
            db,
            workspace_id=workspace.id,
            action="infrastructure",
            context={
                "environment": {
                    "slug": environment.slug,
                    "type": environment.environment_type,
                    "protected": environment.protected,
                },
                "infrastructure": {
                    "tool": target.tool,
                    "source_ref": run.source_ref,
                    "plan_has_changes": run.plan_has_changes,
                    "resource_additions": run.resource_additions,
                    "resource_changes": run.resource_changes,
                    "resource_destroys": run.resource_destroys,
                    "quota_usage": run.quota_usage,
                },
            },
            subject_type="infrastructure_run",
            subject_id=run.id,
            environment_id=environment.id,
        )
        evaluation.created_at = run.created_at
        _align_audit_event(
            db,
            action="policy.evaluated",
            resource_id=str(run.id),
            created_at=run.created_at,
        )


def _create_observability(
    db: Session,
    workspace: Workspace,
    environments: dict[str, Environment],
    services: dict[str, Service],
    now: datetime,
) -> tuple[list[ServiceLevelObjective], list[TelemetrySource]]:
    source_specs = (
        ("checkout-api", "checkout-api", "checkout-request-metrics", 9_000),
        ("payment-ledger", "payment-ledger", "ledger-request-metrics", 12_000),
        ("customer-portal", "customer-portal", "storefront-request-metrics", 2_500),
    )
    sources = []
    source_metadata = []
    for service_slug, _, name, request_base in source_specs:
        token = secrets.token_hex(32)
        source = TelemetrySource(
            workspace_id=workspace.id,
            service_id=services[service_slug].id,
            environment_id=environments["production"].id,
            name=name,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            token_prefix=token[:12],
            enabled=True,
            created_by=None,
            created_at=now - timedelta(days=35),
            last_seen_at=now - timedelta(minutes=6),
        )
        sources.append(source)
        source_metadata.append((source, service_slug, request_base))
    db.add_all(sources)
    db.flush()

    samples = []
    for source, service_slug, request_base in source_metadata:
        for hours_ago in range(31 * 24, -1, -1):
            observed_at = now - timedelta(hours=hours_ago)
            request_count = request_base + (hours_ago % 6) * max(40, request_base // 90)
            if service_slug == "checkout-api":
                error_count = 3 + hours_ago % 4
                buckets = {
                    "100": request_count - 700,
                    "300": request_count - 70 - hours_ago % 12,
                    "500": request_count - 28,
                    "1000": request_count - 8,
                    "2500": request_count,
                    "5000": request_count,
                }
            elif service_slug == "payment-ledger":
                error_count = 48 + (hours_ago % 4) * 3 if hours_ago < 6 else hours_ago % 2
                buckets = {
                    "100": request_count - 1_200,
                    "300": request_count - 48,
                    "500": request_count - 18,
                    "1000": request_count - 4,
                    "2500": request_count,
                    "5000": request_count,
                }
            else:
                error_count = 1 + hours_ago % 2
                buckets = {
                    "100": request_count - 230,
                    "300": request_count - 38 - hours_ago % 8,
                    "500": request_count - 14,
                    "1000": request_count - 4,
                    "2500": request_count,
                    "5000": request_count,
                }
            key = hashlib.sha256(
                f"{source.id}:{observed_at.isoformat()}".encode()
            ).hexdigest()[:48]
            samples.append(
                TelemetrySample(
                    workspace_id=workspace.id,
                    source_id=source.id,
                    service_id=source.service_id,
                    environment_id=source.environment_id,
                    idempotency_key=key,
                    observed_at=observed_at,
                    interval_seconds=3600,
                    request_count=request_count,
                    error_count=error_count,
                    latency_bucket_counts=buckets,
                    created_at=observed_at,
                )
            )
    db.add_all(samples)

    slo_specs = (
        (
            "checkout-availability",
            "Checkout API availability",
            "checkout-api",
            "availability",
            0.999,
            24,
            None,
        ),
        (
            "ledger-availability",
            "Payment Ledger availability",
            "payment-ledger",
            "availability",
            0.9999,
            24,
            None,
        ),
        (
            "storefront-latency",
            "Storefront p95 latency",
            "customer-portal",
            "latency",
            0.98,
            24,
            300,
        ),
    )
    slos = []
    for slug, name, service_slug, indicator, objective, hours, threshold in slo_specs:
        slos.append(
            ServiceLevelObjective(
                workspace_id=workspace.id,
                service_id=services[service_slug].id,
                environment_id=environments["production"].id,
                slug=slug,
                name=name,
                indicator=indicator,
                objective=objective,
                window_hours=hours,
                latency_threshold_ms=threshold,
                enabled=True,
                created_by=None,
                created_at=now - timedelta(days=32),
                last_evaluated_at=now,
            )
        )
    db.add_all(slos)
    db.flush()
    for slo in slos:
        for days_ago in (30, 21, 14, 7, 3, 1, 0):
            evaluation_time = now - timedelta(days=days_ago)
            evaluate_slo(db, slo, now=evaluation_time)
            for event in tuple(db.new):
                if (
                    isinstance(event, AuditEvent)
                    and event.action in {"slo.alert.fired", "slo.alert.auto_resolved"}
                    and event.details.get("slo_id") == str(slo.id)
                ):
                    event.created_at = evaluation_time
    return slos, sources


def _ensure_policies(db: Session, workspace: Workspace, now: datetime) -> list[Policy]:
    existing = {
        policy.slug: policy
        for policy in db.scalars(select(Policy).where(Policy.workspace_id == workspace.id)).all()
    }
    for spec in POLICY_SPECS:
        if spec["slug"] in existing:
            continue
        policy = Policy(
            workspace_id=workspace.id,
            slug=spec["slug"],
            name=spec["name"],
            description=spec["description"],
            enabled=True,
            enforcement_mode="enforce",
            actions=spec["actions"],
            rules=spec["rules"],
            version=1,
            created_by=None,
            updated_by=None,
            created_at=now - timedelta(days=31),
            updated_at=now - timedelta(days=1),
        )
        db.add(policy)
        existing[policy.slug] = policy
    db.flush()
    return [existing[spec["slug"]] for spec in POLICY_SPECS]


def _ensure_demo_members(db: Session, workspace: Workspace, now: datetime) -> None:
    members = db.scalars(
        select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == workspace.id)
    ).all()
    if len(members) >= DEMO_MEMBER_TARGET:
        return
    existing_demo_users = {
        user.subject: user
        for user in db.scalars(select(User).where(User.issuer == DEMO_MEMBER_ISSUER)).all()
    }
    first_names = (
        "Avery", "Jordan", "Morgan", "Riley", "Casey", "Taylor", "Cameron", "Jamie",
        "Quinn", "Reese", "Alex", "Drew", "Robin", "Skyler", "Hayden", "Emerson",
        "Finley", "Rowan", "Sage", "Parker",
    )
    last_names = (
        "Chen", "Patel", "Kim", "Garcia", "Nguyen", "Brooks", "Shah", "Rivera",
        "Foster", "Singh", "Morgan", "Reed", "Bennett", "Ward", "Price", "Cole",
        "Morris", "Diaz", "Baker", "Ross",
    )
    new_memberships = []
    ordinal = 1
    while len(members) + len(new_memberships) < DEMO_MEMBER_TARGET:
        subject = f"northstar-demo-{ordinal:03d}"
        user = existing_demo_users.get(subject)
        if user is None:
            first = first_names[(ordinal - 1) % len(first_names)]
            last = last_names[((ordinal - 1) * 7) % len(last_names)]
            user = User(
                issuer=DEMO_MEMBER_ISSUER,
                subject=subject,
                email=f"{first.lower()}.{last.lower()}.{ordinal:03d}@northstar.example",
                display_name=f"{first} {last}",
                email_verified=False,
                is_platform_admin=False,
                active=False,
                created_at=now - timedelta(days=ordinal % 90),
                updated_at=now - timedelta(days=ordinal % 90),
            )
            db.add(user)
            db.flush()
            existing_demo_users[subject] = user
        has_membership = any(member.user_id == user.id for member in members)
        if not has_membership:
            new_memberships.append(
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role="auditor",
                    granted_by=None,
                    created_at=now - timedelta(days=ordinal % 90),
                )
            )
        ordinal += 1
    db.add_all(new_memberships)
    db.flush()


def _ensure_policy_exceptions(
    db: Session,
    workspace: Workspace,
    policies: list[Policy],
    services: dict[str, Service],
    environments: dict[str, Environment],
    now: datetime,
) -> None:
    service_rows = list(services.values())
    environment_rows = list(environments.values())
    status_cycle = ("approved", "rejected", "expired", "approved", "revoked")
    per_policy_target = max(
        1, (DEMO_POLICY_EXCEPTION_TARGET + max(1, len(policies)) - 1) // max(1, len(policies))
    )
    for policy_index, policy in enumerate(policies):
        current_count = db.scalar(
            select(func.count(PolicyException.id)).where(
                PolicyException.workspace_id == workspace.id,
                PolicyException.policy_id == policy.id,
            )
        ) or 0
        if current_count >= per_policy_target:
            continue
        rule = (policy.rules or [{}])[0]
        rule_id = rule.get("id", "demo-review-rule")
        new_rows = []
        for ordinal in range(current_count, per_policy_target):
            service = service_rows[(ordinal + policy_index * 17) % len(service_rows)]
            environment = environment_rows[(ordinal + policy_index) % len(environment_rows)]
            status = status_cycle[(ordinal + policy_index) % len(status_cycle)]
            if status == "expired":
                created_at = now - timedelta(days=35 + ordinal % 35, hours=ordinal % 24)
                expires_at = created_at + timedelta(days=30)
            else:
                created_at = now - timedelta(days=2 + ordinal % 25, hours=ordinal % 24)
                expires_at = now + timedelta(days=5 + ordinal % 25)
            reviewed_at = created_at + timedelta(hours=4 + ordinal % 20)
            new_rows.append(
                PolicyException(
                    workspace_id=workspace.id,
                    policy_id=policy.id,
                    policy_version=policy.version,
                    rule_id=rule_id,
                    service_id=service.id,
                    environment_id=environment.id,
                    reason=(
                        f"Demo exception {ordinal + 1:03d}: time-bounded review for "
                        f"{service.name} in {environment.name}."
                    ),
                    status=status,
                    requested_by=None,
                    reviewed_by=None,
                    review_comment=(
                        "Sample independent review completed."
                        if status in {"approved", "rejected", "revoked"}
                        else None
                    ),
                    expires_at=expires_at,
                    reviewed_at=reviewed_at if status in {"approved", "rejected", "revoked"} else None,
                    created_at=created_at,
                )
            )
        db.add_all(new_rows)
    db.flush()


def _ensure_observability_volume(
    db: Session,
    workspace: Workspace,
    environments: dict[str, Environment],
    services: dict[str, Service],
    now: datetime,
) -> tuple[list[ServiceLevelObjective], list[TelemetrySource]]:
    production = environments["production"]
    existing_sources = db.scalars(
        select(TelemetrySource).where(TelemetrySource.workspace_id == workspace.id)
    ).all()
    source_by_service = {}
    for source in existing_sources:
        source_by_service.setdefault(source.service_id, source)

    existing_slos = db.scalars(
        select(ServiceLevelObjective).where(ServiceLevelObjective.workspace_id == workspace.id)
    ).all()
    slo_by_service = {}
    for slo in existing_slos:
        slo_by_service.setdefault(slo.service_id, slo)

    core_source_names = {
        "checkout-api": ("checkout-request-metrics", 9_000),
        "payment-ledger": ("ledger-request-metrics", 12_000),
        "customer-portal": ("storefront-request-metrics", 2_500),
    }
    core_slos = {
        "checkout-api": ("checkout-availability", "Checkout API availability", "availability", 0.999, None),
        "payment-ledger": ("ledger-availability", "Payment Ledger availability", "availability", 0.9999, None),
        "customer-portal": ("storefront-latency", "Storefront p95 latency", "latency", 0.98, 300),
    }
    sources_by_slug = {}
    slos_by_slug = {}
    for service_index, spec in enumerate(ALL_SERVICE_SPECS):
        service = services[spec["slug"]]
        source = source_by_service.get(service.id)
        if source is None:
            name, _request_base = core_source_names.get(
                spec["slug"], (f"{spec['slug']}-otel-metrics", 4_000 + service_index * 37)
            )
            token = hashlib.sha256(
                f"deployforge-demo:{workspace.id}:{service.slug}".encode()
            ).hexdigest()
            source = TelemetrySource(
                workspace_id=workspace.id,
                service_id=service.id,
                environment_id=production.id,
                name=name,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                token_prefix=token[:12],
                enabled=True,
                created_by=None,
                created_at=now - timedelta(days=35),
                last_seen_at=now - timedelta(minutes=6 + service_index % 20),
            )
            db.add(source)
            db.flush()
            source_by_service[service.id] = source
        sources_by_slug[spec["slug"]] = source

        slo = slo_by_service.get(service.id)
        if slo is None:
            if spec["slug"] in core_slos:
                slug, name, indicator, objective, threshold = core_slos[spec["slug"]]
            else:
                indicator = "availability"
                objective = 0.9999 if service_index % 5 == 0 else 0.999
                threshold = None
                slug = f"{service.slug}-availability"
                name = f"{service.name} availability"
            slo = ServiceLevelObjective(
                workspace_id=workspace.id,
                service_id=service.id,
                environment_id=production.id,
                slug=slug,
                name=name,
                indicator=indicator,
                objective=objective,
                window_hours=24,
                latency_threshold_ms=threshold,
                enabled=True,
                created_by=None,
                created_at=now - timedelta(days=32),
                last_evaluated_at=None,
            )
            db.add(slo)
            db.flush()
            slo_by_service[service.id] = slo
        slos_by_slug[spec["slug"]] = slo

    # Add a small, varied sample set per service. Two time windows create both
    # resolved history and a few current alerts without generating huge telemetry volumes.
    for service_index, spec in enumerate(ALL_SERVICE_SPECS):
        service = services[spec["slug"]]
        source = sources_by_slug[spec["slug"]]
        latest_sample = db.scalar(
            select(func.max(TelemetrySample.observed_at)).where(
                TelemetrySample.source_id == source.id
            )
        )
        if latest_sample is not None:
            latest_sample = _utc_datetime(latest_sample)
        if latest_sample is not None and latest_sample >= now - timedelta(hours=54):
            continue
        request_base = core_source_names.get(spec["slug"], (None, 4_000 + service_index * 37))[1]
        current_error_count = 80 if service_index % 6 == 0 else 0
        sample_rows = []
        for hours_ago, error_count, latency_good_ratio in (
            (40, 50, 0.95),
            (32, 50, 0.95),
            (12, current_error_count, 0.997 if service_index % 6 == 0 else 0.9998),
            (2, current_error_count, 0.997 if service_index % 6 == 0 else 0.9998),
        ):
            observed_at = now - timedelta(hours=hours_ago)
            key = hashlib.sha256(f"{source.id}:{observed_at.isoformat()}".encode()).hexdigest()[:48]
            good_latency_count = int(request_base * latency_good_ratio)
            sample_rows.append(
                TelemetrySample(
                    workspace_id=workspace.id,
                    source_id=source.id,
                    service_id=service.id,
                    environment_id=production.id,
                    idempotency_key=key,
                    observed_at=observed_at,
                    interval_seconds=3600,
                    request_count=request_base,
                    error_count=error_count,
                    latency_bucket_counts={
                        "100": int(request_base * 0.65),
                        "300": good_latency_count,
                        "500": int(request_base * 0.996),
                        "1000": request_base,
                        "2500": request_base,
                        "5000": request_base,
                    },
                    created_at=observed_at,
                )
            )
        db.add_all(sample_rows)
        db.flush()

    for slo in slos_by_slug.values():
        evaluation_count = db.scalar(
            select(func.count(SloEvaluation.id)).where(SloEvaluation.slo_id == slo.id)
        ) or 0
        if evaluation_count > 0:
            continue
        evaluate_slo(db, slo, now=now - timedelta(hours=30))
        evaluate_slo(db, slo, now=now)
        for event in tuple(db.new):
            if (
                isinstance(event, AuditEvent)
                and event.action in {"slo.alert.fired", "slo.alert.auto_resolved"}
                and event.details.get("slo_id") == str(slo.id)
            ):
                event.details = _demo_details(**(event.details or {}))
    db.flush()

    all_slos = db.scalars(
        select(ServiceLevelObjective)
        .where(ServiceLevelObjective.workspace_id == workspace.id)
        .order_by(ServiceLevelObjective.name, ServiceLevelObjective.id)
    ).all()
    all_sources = db.scalars(
        select(TelemetrySource)
        .where(TelemetrySource.workspace_id == workspace.id)
        .order_by(TelemetrySource.created_at, TelemetrySource.id)
    ).all()
    return all_slos, all_sources


def _create_audit_history(
    db: Session,
    workspace: Workspace,
    deployments: list[Deployment],
    builds: dict,
    gitops_runs: list[GitOpsRun],
    infra_runs: list[InfrastructureRun],
    policies: list[Policy],
    slos: list[ServiceLevelObjective],
    now: datetime,
) -> None:
    examples = []
    existing_keys = {
        (event.action, event.resource_type, event.resource_id)
        for event in db.scalars(
            select(AuditEvent).where(AuditEvent.workspace_id == workspace.id)
        ).all()
    }
    for index, deployment in enumerate(deployments):
        service = db.get(Service, deployment.service_id)
        examples.append(
            (
                "deployment.failed" if deployment.status == "failed" else "deployment.completed",
                "deployment",
                deployment.id,
                (
                    "failed"
                    if deployment.status in {"failed", "rejected"}
                    else "canceled"
                    if deployment.status == "canceled"
                    else "succeeded"
                ),
                f"{service.name} {deployment.version} sample release recorded.",
                index,
            )
        )
    for index, build in enumerate(builds.values()):
        examples.append(
            (
                "build.completed",
                "build_run",
                build.id,
                build.status,
                "Sample CI workflow reached a terminal state.",
                index + 2,
            )
        )
    for index, run in enumerate(gitops_runs):
        examples.append(
            (
                "gitops.reconciled",
                "gitops_run",
                run.id,
                run.status,
                f"Sample reconciliation recorded for {run.application_name}.",
                index + 3,
            )
        )
    for index, run in enumerate(infra_runs):
        examples.append(
            (
                "infrastructure.run.completed",
                "infrastructure_run",
                run.id,
                run.status,
                f"Sample {run.run_kind.replace('_', ' ')} recorded for {run.working_directory}.",
                index + 4,
            )
        )
    for index, policy in enumerate(policies):
        examples.append(
            (
                "policy.created",
                "policy",
                policy.id,
                "succeeded",
                f"{policy.name} sample guardrail was published.",
                index + 1,
            )
        )
    for index, slo in enumerate(slos):
        examples.append(
            (
                "slo.evaluation.completed",
                "service_level_objective",
                slo.id,
                "succeeded",
                f"{slo.name} sample reliability evaluation recorded.",
                index + 1,
            )
        )
    examples.append(
        (
            "workspace.demo_seeded",
            "workspace",
            workspace.id,
            "succeeded",
            "Synthetic walkthrough dataset initialized in this demo workspace.",
            0,
        )
    )
    for index, (action, resource_type, resource_id, outcome, message, offset) in enumerate(
        examples
    ):
        key = (action, resource_type, str(resource_id))
        if key in existing_keys:
            continue
        age_days = 0 if action == "workspace.demo_seeded" else (index * 7 + offset) % 38
        db.add(
            AuditEvent(
                workspace_id=workspace.id,
                actor_type="system",
                actor_id="demo-seed",
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id),
                outcome=outcome,
                details=_demo_details(summary=message),
                created_at=now - timedelta(days=age_days, minutes=index * 5),
            )
        )
        existing_keys.add(key)


def seed_demo_workspace(db: Session, *, email: str) -> tuple[Workspace, bool]:
    """Create the demo workspace and give its local operator platform-engineer access."""
    _require_local_seed_mode()
    normalized_email = str(TypeAdapter(EmailStr).validate_python(email.strip())).casefold()
    user = db.scalar(select(User).where(func.lower(User.email) == normalized_email))
    if user is None or not user.active or user.issuer != LOCAL_ISSUER:
        raise ValueError(
            "An existing active local account is required to access the demo workspace"
        )

    try:
        # Golden Paths are global platform configuration; keep these in sync with app defaults.
        seed_builtin_golden_paths(db)
        workspace = db.scalar(select(Workspace).where(Workspace.slug == SEED_SLUG))
        created = workspace is None
        if workspace is not None and (
            not workspace.description or SEED_MARKER not in workspace.description
        ):
            raise ValueError(f"Workspace slug '{SEED_SLUG}' already belongs to another workspace")
        if workspace is None:
            description = (
                "Synthetic services and operational history for a local walkthrough. "
                "Provider jobs are not connected. "
                f"{SEED_MARKER}"
            )
            workspace = Workspace(slug=SEED_SLUG, name=DEMO_NAME, description=description)
            db.add(workspace)
            db.flush()
        _ensure_demo_operator_membership(db, workspace, user)

        now = datetime.now(UTC).replace(microsecond=0)
        environments, _teams, services = _seed_environment_and_services(db, workspace, now)
        _ensure_demo_members(db, workspace, now)
        policies = _ensure_policies(db, workspace, now)
        deployments = _create_deployments(db, workspace, environments, services, now)
        builds, artifacts = _create_builds_and_artifacts(db, workspace, services, deployments, now)
        _gitops_targets, gitops_runs = _create_gitops(
            db, workspace, environments, services, deployments, builds, artifacts, now
        )
        infrastructure_targets, infrastructure_runs = _create_infrastructure(
            db, workspace, environments, now
        )
        _evaluate_infrastructure_policies(
            db, workspace, environments, infrastructure_targets, infrastructure_runs
        )
        _ensure_policy_exceptions(db, workspace, policies, services, environments, now)
        slos, _telemetry_sources = _ensure_observability_volume(
            db, workspace, environments, services, now
        )
        _create_audit_history(
            db,
            workspace,
            deployments,
            builds,
            gitops_runs,
            infrastructure_runs,
            policies,
            slos,
            now,
        )
        # Mark evaluator-generated audit records too; no sample action impersonates a person.
        for event in db.scalars(select(AuditEvent).where(AuditEvent.workspace_id == workspace.id)):
            event.details = _demo_details(**(event.details or {}))
            if event.actor_type == "system" and event.actor_id is None:
                event.actor_id = "demo-seed"

        db.commit()
        return workspace, created
    except Exception:
        db.rollback()
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed a synthetic local DeployForge workspace")
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed", help="Create the Northstar Commerce Demo workspace")
    seed.add_argument("--email", required=True, help="Existing local account to add to the demo workspace")
    args = parser.parse_args(argv)
    try:
        with SessionLocal() as db:
            workspace, created = seed_demo_workspace(db, email=args.email)
            if created:
                print(
                    f"Created {workspace.name} with {len(ALL_SERVICE_SPECS)} services, "
                    f"{len(_all_deployment_specs())} releases and 100 infrastructure runs."
                )
                print(
                    "The account has platform-engineer access only in the demo workspace; external provider jobs are disabled."
                )
            else:
                print(
                    f"{workspace.name} was refreshed to 100+ operational records per demo table; "
                    "existing synthetic rows were reused."
                )
        return 0
    except (ValueError, ValidationError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
