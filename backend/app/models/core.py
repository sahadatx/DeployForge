import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("slug", name="uq_workspaces_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    environments: Mapped[list["Environment"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    services: Mapped[list["Service"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    memberships: Mapped[list["WorkspaceMembership"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_active_email", "active", "email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(180), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(512))
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    scim_external_id: Mapped[str | None] = mapped_column(String(255))
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    platform_admin_source: Mapped[str] = mapped_column(
        String(32), default="none", server_default="none", nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    memberships: Mapped[list["WorkspaceMembership"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="WorkspaceMembership.user_id",
    )
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    scim_group_memberships: Mapped[list["SCIMGroupMembership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class SCIMGroup(Base):
    __tablename__ = "scim_groups"
    __table_args__ = (UniqueConstraint("display_name", name="uq_scim_groups_display_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    memberships: Mapped[list["SCIMGroupMembership"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class SCIMGroupMembership(Base):
    __tablename__ = "scim_group_memberships"
    __table_args__ = (Index("ix_scim_group_memberships_user_id", "user_id"),)

    group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("scim_groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    group: Mapped[SCIMGroup] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="scim_group_memberships")


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_memberships_workspace_user"),
        Index("ix_workspace_memberships_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    identity_source: Mapped[str] = mapped_column(
        String(40), default="manual", server_default="manual", nullable=False
    )
    identity_groups: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships", foreign_keys=[user_id])


class Environment(Base):
    __tablename__ = "environments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_environments_workspace_slug"),
        Index("ix_environments_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    environment_type: Mapped[str] = mapped_column(String(32), nullable=False)
    cluster_name: Mapped[str | None] = mapped_column(String(180))
    namespace: Mapped[str | None] = mapped_column(String(63))
    protected: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    allow_destroy: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    quota_cpu_millis: Mapped[int | None] = mapped_column()
    quota_memory_mib: Mapped[int | None] = mapped_column()
    quota_pods: Mapped[int | None] = mapped_column()
    quota_persistent_volume_claims: Mapped[int | None] = mapped_column()
    quota_services: Mapped[int | None] = mapped_column()
    deployment_freeze_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deployment_freeze_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deployment_freeze_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    workspace: Mapped[Workspace] = relationship(back_populates="environments")
    deployments: Mapped[list["Deployment"]] = relationship(back_populates="environment")
    gitops_targets: Mapped[list["GitOpsTarget"]] = relationship(back_populates="environment")


class ServiceTeam(Base):
    __tablename__ = "service_teams"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_service_teams_workspace_slug"),
        Index("ix_service_teams_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    workspace: Mapped[Workspace] = relationship()
    services: Mapped[list["Service"]] = relationship(back_populates="team")


class GoldenPath(Base):
    __tablename__ = "golden_paths"
    __table_args__ = (UniqueConstraint("slug", name="uq_golden_paths_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    icon: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="available", nullable=False)
    capabilities: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    config_schema: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    defaults: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    services: Mapped[list["Service"]] = relationship(back_populates="golden_path")


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_services_workspace_slug"),
        Index("ix_services_workspace_id", "workspace_id"),
        Index("ix_services_owner_team_id", "owner_team_id"),
        Index("ix_services_lifecycle", "workspace_id", "lifecycle"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    system: Mapped[str | None] = mapped_column(String(120))
    owner_team_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("service_teams.id", ondelete="RESTRICT")
    )
    repository_url: Mapped[str | None] = mapped_column(String(500))
    documentation_url: Mapped[str | None] = mapped_column(String(500))
    lifecycle: Mapped[str] = mapped_column(String(32), default="experimental", nullable=False)
    criticality: Mapped[str] = mapped_column(
        String(24), default="tier_2", server_default="tier_2", nullable=False
    )
    tags: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'"), nullable=False
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON, default=dict, server_default=text("'{}'"), nullable=False
    )
    golden_path_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("golden_paths.id", ondelete="SET NULL")
    )
    golden_path_snapshot: Mapped[dict | None] = mapped_column(JSON)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
        nullable=False,
    )

    workspace: Mapped[Workspace] = relationship(back_populates="services")
    team: Mapped[ServiceTeam | None] = relationship(back_populates="services")
    golden_path: Mapped[GoldenPath | None] = relationship(back_populates="services")
    deployments: Mapped[list["Deployment"]] = relationship(back_populates="service")
    gitops_targets: Mapped[list["GitOpsTarget"]] = relationship(back_populates="service")
    repository_provisionings: Mapped[list["GitHubRepositoryProvisioning"]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
        order_by="GitHubRepositoryProvisioning.created_at",
    )


class GitHubRepositoryProvisioning(Base):
    """Durable request to generate a service repository from a trusted template."""

    __tablename__ = "github_repository_provisionings"
    __table_args__ = (
        Index("ix_github_repository_provisionings_status_available", "status", "available_at"),
        Index("ix_github_repository_provisionings_workspace_created", "workspace_id", "created_at"),
        Index(
            "uq_github_repository_provisionings_service",
            "service_id",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    golden_path_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    repository_owner: Mapped[str] = mapped_column(String(100), nullable=False)
    repository_name: Mapped[str] = mapped_column(String(100), nullable=False)
    template_owner: Mapped[str] = mapped_column(String(100), nullable=False)
    template_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(default=5, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_repository_id: Mapped[str | None] = mapped_column(String(80))
    repository_url: Mapped[str | None] = mapped_column(String(500))
    default_branch: Mapped[str | None] = mapped_column(String(255))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    workspace: Mapped[Workspace] = relationship()
    service: Mapped[Service] = relationship(back_populates="repository_provisionings")


class Deployment(Base):
    __tablename__ = "deployments"
    __table_args__ = (
        Index("ix_deployments_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_deployments_service_id", "service_id"),
        Index("ix_deployments_promoted_artifact_id", "promoted_artifact_id"),
        Index("ix_deployments_status_available_at", "status", "available_at"),
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_deployments_workspace_idempotency"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("services.id", ondelete="RESTRICT"), nullable=False
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(160), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(255))
    promoted_artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    promotion_gitops_revision: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(180))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(default=3, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    service: Mapped[Service] = relationship(back_populates="deployments")
    environment: Mapped[Environment] = relationship(back_populates="deployments")
    events: Mapped[list["DeploymentEvent"]] = relationship(
        back_populates="deployment",
        cascade="all, delete-orphan",
        order_by="DeploymentEvent.sequence",
    )
    build_runs: Mapped[list["BuildRun"]] = relationship(
        back_populates="deployment", cascade="all, delete-orphan"
    )


class DeploymentEvent(Base):
    __tablename__ = "deployment_events"
    __table_args__ = (
        UniqueConstraint("deployment_id", "sequence", name="uq_deployment_events_sequence"),
        Index("ix_deployment_events_workspace_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str | None] = mapped_column(String(40))
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(180))
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    deployment: Mapped[Deployment] = relationship(back_populates="events")


class BuildRun(Base):
    __tablename__ = "build_runs"
    __table_args__ = (
        UniqueConstraint(
            "deployment_id", "attempt_number", name="uq_build_runs_deployment_attempt"
        ),
        Index("ix_build_runs_status_available_at", "status", "available_at"),
        Index("ix_build_runs_workspace_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("services.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    attempt_number: Mapped[int] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(40), default="github_actions", nullable=False)
    repository_url: Mapped[str | None] = mapped_column(String(500))
    workflow_path: Mapped[str] = mapped_column(String(255), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    gitops_revision: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(default=3, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_run_id: Mapped[str | None] = mapped_column(String(120))
    run_url: Mapped[str | None] = mapped_column(String(1000))
    callback_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    deployment: Mapped[Deployment] = relationship(back_populates="build_runs")
    events: Mapped[list["BuildRunEvent"]] = relationship(
        back_populates="build_run",
        cascade="all, delete-orphan",
        order_by="BuildRunEvent.sequence",
    )
    artifacts: Mapped[list["BuildArtifact"]] = relationship(
        back_populates="build_run", cascade="all, delete-orphan"
    )


class BuildRunEvent(Base):
    __tablename__ = "build_run_events"
    __table_args__ = (
        UniqueConstraint("build_run_id", "sequence", name="uq_build_run_events_sequence"),
        UniqueConstraint("build_run_id", "delivery_id", name="uq_build_run_events_delivery"),
        Index("ix_build_run_events_workspace_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    build_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("build_runs.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    delivery_id: Mapped[str | None] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str | None] = mapped_column(String(40))
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(180))
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    build_run: Mapped[BuildRun] = relationship(back_populates="events")


class BuildArtifact(Base):
    __tablename__ = "build_artifacts"
    __table_args__ = (
        UniqueConstraint("build_run_id", "digest", name="uq_build_artifacts_run_digest"),
        Index("ix_build_artifacts_workspace_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    build_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("build_runs.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(40), nullable=False)
    uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    digest: Mapped[str] = mapped_column(String(80), nullable=False)
    sbom_format: Mapped[str] = mapped_column(String(40), nullable=False)
    sbom_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    sbom_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    vulnerability_uri: Mapped[str | None] = mapped_column(String(1000))
    vulnerability_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    vulnerability_report_digest: Mapped[str | None] = mapped_column(String(80))
    vulnerability_scanner_version: Mapped[str | None] = mapped_column(String(100))
    signature_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    signer_identity: Mapped[str] = mapped_column(String(500), nullable=False)
    oidc_issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    provenance_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    provenance_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_status: Mapped[str] = mapped_column(
        String(40), default="workflow_reported", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    build_run: Mapped[BuildRun] = relationship(back_populates="artifacts")


class GitOpsTarget(Base):
    __tablename__ = "gitops_targets"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "service_id",
            "environment_id",
            name="uq_gitops_targets_service_environment",
        ),
        UniqueConstraint(
            "workspace_id", "application_name", name="uq_gitops_targets_workspace_application"
        ),
        Index("ix_gitops_targets_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("environments.id", ondelete="CASCADE"), nullable=False
    )
    application_name: Mapped[str] = mapped_column(String(253), nullable=False)
    provider: Mapped[str] = mapped_column(
        String(24), default="argocd", server_default=text("'argocd'"), nullable=False
    )
    expected_project: Mapped[str] = mapped_column(String(80), default="default", nullable=False)
    expected_cluster_name: Mapped[str | None] = mapped_column(String(180))
    reconciliation_namespace: Mapped[str | None] = mapped_column(String(63))
    artifact_name: Mapped[str | None] = mapped_column(String(180))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    service: Mapped[Service] = relationship(back_populates="gitops_targets")
    environment: Mapped[Environment] = relationship(back_populates="gitops_targets")
    runs: Mapped[list["GitOpsRun"]] = relationship(back_populates="target")


class GitOpsRun(Base):
    __tablename__ = "gitops_runs"
    __table_args__ = (
        UniqueConstraint(
            "deployment_id", "attempt_number", name="uq_gitops_runs_deployment_attempt"
        ),
        Index("ix_gitops_runs_status_next_poll", "status", "next_poll_at"),
        Index("ix_gitops_runs_target_created_at", "target_id", "created_at"),
        Index("ix_gitops_runs_workspace_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False
    )
    build_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("build_runs.id", ondelete="SET NULL")
    )
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("gitops_targets.id", ondelete="SET NULL")
    )
    rollback_from_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("gitops_runs.id", ondelete="SET NULL")
    )
    attempt_number: Mapped[int] = mapped_column(nullable=False)
    action_kind: Mapped[str] = mapped_column(String(24), default="deploy", nullable=False)
    application_name: Mapped[str] = mapped_column(String(253), nullable=False)
    desired_revision: Mapped[str | None] = mapped_column(String(255))
    observed_revision: Mapped[str | None] = mapped_column(String(255))
    artifact_uri: Mapped[str | None] = mapped_column(String(1000))
    artifact_digest: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    sync_status: Mapped[str | None] = mapped_column(String(40))
    health_status: Mapped[str | None] = mapped_column(String(40))
    operation_phase: Mapped[str | None] = mapped_column(String(40))
    destination_name: Mapped[str | None] = mapped_column(String(180))
    destination_server: Mapped[str | None] = mapped_column(String(500))
    attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(default=3, nullable=False)
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    callback_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rollout_snapshot: Mapped[dict | None] = mapped_column(JSON)
    rollout_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    deployment: Mapped[Deployment] = relationship()
    build_run: Mapped[BuildRun | None] = relationship()
    target: Mapped[GitOpsTarget | None] = relationship(back_populates="runs")
    rollback_from_run: Mapped["GitOpsRun | None"] = relationship(remote_side="GitOpsRun.id")
    events: Mapped[list["GitOpsRunEvent"]] = relationship(
        back_populates="gitops_run",
        cascade="all, delete-orphan",
        order_by="GitOpsRunEvent.sequence",
    )
    rollout_actions: Mapped[list["GitOpsRolloutAction"]] = relationship(
        back_populates="gitops_run",
        cascade="all, delete-orphan",
        order_by="GitOpsRolloutAction.created_at",
    )


class GitOpsRunEvent(Base):
    __tablename__ = "gitops_run_events"
    __table_args__ = (
        UniqueConstraint("gitops_run_id", "sequence", name="uq_gitops_run_events_sequence"),
        Index("ix_gitops_run_events_workspace_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    gitops_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("gitops_runs.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str | None] = mapped_column(String(40))
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(180))
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    gitops_run: Mapped[GitOpsRun] = relationship(back_populates="events")


class GitOpsRolloutAction(Base):
    """Durable, audited manual step promotion and abort operations."""

    __tablename__ = "gitops_rollout_actions"
    __table_args__ = (
        Index("ix_gitops_rollout_actions_status_available", "status", "available_at"),
        Index("ix_gitops_rollout_actions_workspace_created", "workspace_id", "created_at"),
        Index(
            "uq_gitops_rollout_actions_active_run",
            "gitops_run_id",
            unique=True,
            sqlite_where=text("status IN ('queued', 'dispatching')"),
            postgresql_where=text("status IN ('queued', 'dispatching')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    gitops_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("gitops_runs.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    comment: Mapped[str] = mapped_column(String(1000), nullable=False)
    requested_step: Mapped[str | None] = mapped_column(String(180))
    requested_rollout_status: Mapped[str | None] = mapped_column(String(40))
    attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(default=5, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_message: Mapped[str | None] = mapped_column(Text)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    gitops_run: Mapped[GitOpsRun] = relationship(back_populates="rollout_actions")


class InfrastructureTarget(Base):
    __tablename__ = "infrastructure_targets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "environment_id", name="uq_infra_targets_environment"),
        Index("ix_infrastructure_targets_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("environments.id", ondelete="CASCADE"), nullable=False
    )
    repository_url: Mapped[str] = mapped_column(String(500), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), default="main", nullable=False)
    working_directory: Mapped[str] = mapped_column(String(500), default=".", nullable=False)
    tool: Mapped[str] = mapped_column(String(24), default="terraform", nullable=False)
    backend_workspace: Mapped[str | None] = mapped_column(String(180))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class InfrastructureRun(Base):
    __tablename__ = "infrastructure_runs"
    __table_args__ = (
        Index("ix_infrastructure_runs_status_available_at", "status", "available_at"),
        Index("ix_infrastructure_runs_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_infrastructure_runs_environment_created_at", "environment_id", "created_at"),
        Index(
            "uq_infrastructure_runs_active_environment",
            "environment_id",
            unique=True,
            sqlite_where=text(
                "status IN ('queued', 'dispatching', 'dispatched', 'running', "
                "'awaiting_approval', 'apply_queued', 'applying')"
            ),
            postgresql_where=text(
                "status IN ('queued', 'dispatching', 'dispatched', 'running', "
                "'awaiting_approval', 'apply_queued', 'applying')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("environments.id", ondelete="CASCADE"), nullable=False
    )
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("infrastructure_targets.id", ondelete="SET NULL")
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    attempt_number: Mapped[int] = mapped_column(default=1, nullable=False)
    attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(default=3, nullable=False)
    run_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    active_operation: Mapped[str | None] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    working_directory: Mapped[str] = mapped_column(String(500), nullable=False)
    external_run_id: Mapped[str | None] = mapped_column(String(180))
    run_url: Mapped[str | None] = mapped_column(String(1000))
    plan_digest: Mapped[str | None] = mapped_column(String(80))
    plan_artifact_uri: Mapped[str | None] = mapped_column(String(1000))
    plan_has_changes: Mapped[bool | None] = mapped_column(Boolean)
    resource_additions: Mapped[int | None] = mapped_column()
    resource_changes: Mapped[int | None] = mapped_column()
    resource_destroys: Mapped[int | None] = mapped_column()
    quota_usage: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    callback_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    events: Mapped[list["InfrastructureRunEvent"]] = relationship(
        back_populates="infrastructure_run",
        cascade="all, delete-orphan",
        order_by="InfrastructureRunEvent.sequence",
    )


class InfrastructureRunEvent(Base):
    __tablename__ = "infrastructure_run_events"
    __table_args__ = (
        UniqueConstraint("infrastructure_run_id", "sequence", name="uq_infra_run_events_sequence"),
        UniqueConstraint("infrastructure_run_id", "delivery_id", name="uq_infra_run_delivery"),
        Index("ix_infrastructure_run_events_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    infrastructure_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("infrastructure_runs.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    delivery_id: Mapped[str | None] = mapped_column(String(180))
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str | None] = mapped_column(String(40))
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(180))
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    infrastructure_run: Mapped[InfrastructureRun] = relationship(back_populates="events")


class Policy(Base):
    __tablename__ = "policies"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_policies_workspace_slug"),
        Index("ix_policies_workspace_enabled", "workspace_id", "enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enforcement_mode: Mapped[str] = mapped_column(
        String(16), default="enforce", server_default="enforce", nullable=False
    )
    actions: Mapped[list] = mapped_column(JSON, default=lambda: ["deployment"], nullable=False)
    rules: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class PolicyEvaluation(Base):
    __tablename__ = "policy_evaluations"
    __table_args__ = (
        Index("ix_policy_evaluations_workspace_created", "workspace_id", "created_at"),
        Index("ix_policy_evaluations_subject", "subject_type", "subject_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(180), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    policy_snapshot: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    policy_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    results: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class PolicyException(Base):
    __tablename__ = "policy_exceptions"
    __table_args__ = (
        Index("ix_policy_exceptions_workspace_status", "workspace_id", "status"),
        Index("ix_policy_exceptions_policy_rule_status", "policy_id", "rule_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("policies.id", ondelete="CASCADE"), nullable=False
    )
    policy_version: Mapped[int] = mapped_column(nullable=False)
    rule_id: Mapped[str] = mapped_column(String(100), nullable=False)
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("services.id", ondelete="CASCADE")
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("environments.id", ondelete="CASCADE")
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    review_comment: Mapped[str | None] = mapped_column(String(1000))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_audit_events_resource", "resource_type", "resource_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(180))
    action: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(180))
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class AuditExportCursor(Base):
    __tablename__ = "audit_export_cursors"

    export_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    cursor_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor_event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    chain_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_sequence: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class AuditExportBatch(Base):
    """Durable reservation for an immutable audit export object pair."""

    __tablename__ = "audit_export_batches"
    __table_args__ = (
        UniqueConstraint("export_name", "sequence", name="uq_audit_export_batches_sequence"),
        Index("ix_audit_export_batches_status_sequence", "status", "sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    export_name: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("audit_export_cursors.export_name", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    object_stem: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_chain_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    final_chain_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditExportBatchEvent(Base):
    """Exactly-once database reservation of an audit event for an export batch."""

    __tablename__ = "audit_export_batch_events"
    __table_args__ = (
        UniqueConstraint("batch_id", "ordinal", name="uq_audit_export_batch_events_ordinal"),
        Index("ix_audit_export_batch_events_batch_ordinal", "batch_id", "ordinal"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("audit_events.id", ondelete="RESTRICT"), primary_key=True
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("audit_export_batches.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    record: Mapped[dict] = mapped_column(JSON, nullable=False)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TelemetrySource(Base):
    __tablename__ = "telemetry_sources"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_telemetry_sources_token_hash"),
        Index("ix_telemetry_sources_workspace_enabled", "workspace_id", "enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("environments.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TelemetrySample(Base):
    __tablename__ = "telemetry_samples"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "idempotency_key", name="uq_telemetry_samples_source_idempotency"
        ),
        UniqueConstraint(
            "source_id", "observed_at", name="uq_telemetry_samples_source_interval_end"
        ),
        Index("ix_telemetry_samples_service_observed", "service_id", "observed_at"),
        Index("ix_telemetry_samples_workspace_observed", "workspace_id", "observed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("telemetry_sources.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("environments.id", ondelete="CASCADE")
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interval_seconds: Mapped[int] = mapped_column(nullable=False)
    request_count: Mapped[int] = mapped_column(nullable=False)
    error_count: Mapped[int] = mapped_column(nullable=False)
    latency_bucket_counts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ServiceLevelObjective(Base):
    __tablename__ = "service_level_objectives"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_slos_workspace_slug"),
        Index("ix_slos_workspace_enabled", "workspace_id", "enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("environments.id", ondelete="CASCADE")
    )
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    indicator: Mapped[str] = mapped_column(String(24), nullable=False)
    objective: Mapped[float] = mapped_column(nullable=False)
    window_hours: Mapped[int] = mapped_column(nullable=False)
    latency_threshold_ms: Mapped[int | None] = mapped_column()
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SloEvaluation(Base):
    __tablename__ = "slo_evaluations"
    __table_args__ = (
        Index("ix_slo_evaluations_slo_created", "slo_id", "created_at"),
        Index("ix_slo_evaluations_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    slo_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("service_level_objectives.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_events: Mapped[int] = mapped_column(nullable=False)
    good_events: Mapped[int] = mapped_column(nullable=False)
    attainment: Mapped[float | None] = mapped_column()
    error_budget_remaining: Mapped[float | None] = mapped_column()
    burn_rate: Mapped[float | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class SloAlert(Base):
    __tablename__ = "slo_alerts"
    __table_args__ = (
        Index("ix_slo_alerts_workspace_status_created", "workspace_id", "status", "created_at"),
        Index("ix_slo_alerts_slo_status", "slo_id", "status"),
        Index(
            "uq_slo_alerts_active_per_slo",
            "slo_id",
            unique=True,
            sqlite_where=text("status IN ('firing', 'acknowledged')"),
            postgresql_where=text("status IN ('firing', 'acknowledged')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    slo_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("service_level_objectives.id", ondelete="CASCADE"), nullable=False
    )
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("slo_evaluations.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), default="firing", nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pagerduty_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pagerduty_resolved_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pagerduty_last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class Incident(Base):
    """Workspace scoped incident record with deduplicated source and paging delivery."""

    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "source_type", "source_id", name="uq_incidents_source"),
        Index("ix_incidents_workspace_status_created", "workspace_id", "status", "created_at"),
        Index("ix_incidents_workspace_service_created", "workspace_id", "service_id", "created_at"),
        Index("ix_incidents_pagerduty_due", "status", "pagerduty_last_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("services.id", ondelete="SET NULL")
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("environments.id", ondelete="SET NULL")
    )
    source_type: Mapped[str] = mapped_column(String(24), default="manual", nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    severity: Mapped[str] = mapped_column(String(8), default="sev3", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="open", nullable=False)
    resolution: Mapped[str | None] = mapped_column(Text)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    opened_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    escalation_level: Mapped[int] = mapped_column(default=0, nullable=False)
    next_escalation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    mitigated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pagerduty_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pagerduty_resolved_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pagerduty_last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class IncidentEvent(Base):
    """Append-only response timeline; edits are represented as new events."""

    __tablename__ = "incident_events"
    __table_args__ = (
        UniqueConstraint("incident_id", "sequence", name="uq_incident_events_sequence"),
        Index("ix_incident_events_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str | None] = mapped_column(String(24))
    actor_type: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(180))
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class IncidentPostmortem(Base):
    """Reviewable postmortem draft, published only by a different reviewer."""

    __tablename__ = "incident_postmortems"
    __table_args__ = (
        UniqueConstraint("incident_id", name="uq_incident_postmortems_incident"),
        Index("ix_incident_postmortems_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    impact: Mapped[str] = mapped_column(Text, default="", nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, default="", nullable=False)
    detection: Mapped[str] = mapped_column(Text, default="", nullable=False)
    response: Mapped[str] = mapped_column(Text, default="", nullable=False)
    corrective_actions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    review_comment: Mapped[str | None] = mapped_column(String(1000))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
        Index("ix_user_sessions_user_expires", "user_id", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="sessions")
