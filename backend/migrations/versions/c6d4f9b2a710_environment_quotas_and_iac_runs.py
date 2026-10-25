"""Environment protection, quotas, and durable infrastructure runs.

Revision ID: c6d4f9b2a710
Revises: b4e2c8a16d90
Create Date: 2026-10-25 18:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "c6d4f9b2a710"
down_revision = "b4e2c8a16d90"
branch_labels = None
depends_on = None

ACTIVE_INFRA_RUNS = (
    "'queued', 'dispatching', 'dispatched', 'running', "
    "'awaiting_approval', 'apply_queued', 'applying'"
)


def upgrade() -> None:
    op.add_column("environments", sa.Column("cluster_name", sa.String(length=180), nullable=True))
    op.add_column("environments", sa.Column("namespace", sa.String(length=63), nullable=True))
    op.add_column(
        "environments",
        sa.Column("protected", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "environments",
        sa.Column("allow_destroy", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    for name in (
        "quota_cpu_millis",
        "quota_memory_mib",
        "quota_pods",
        "quota_persistent_volume_claims",
        "quota_services",
    ):
        op.add_column("environments", sa.Column(name, sa.Integer(), nullable=True))
    op.execute("UPDATE environments SET protected = true WHERE environment_type = 'production'")

    op.create_table(
        "infrastructure_targets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("repository_url", sa.String(length=500), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=False),
        sa.Column("working_directory", sa.String(length=500), nullable=False),
        sa.Column("tool", sa.String(length=24), nullable=False),
        sa.Column("backend_workspace", sa.String(length=180), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "environment_id", name="uq_infra_targets_environment"),
    )
    op.create_index(
        "ix_infrastructure_targets_workspace_id", "infrastructure_targets", ["workspace_id"]
    )

    op.create_table(
        "infrastructure_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("run_kind", sa.String(length=24), nullable=False),
        sa.Column("active_operation", sa.String(length=24), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=False),
        sa.Column("working_directory", sa.String(length=500), nullable=False),
        sa.Column("external_run_id", sa.String(length=180), nullable=True),
        sa.Column("run_url", sa.String(length=1000), nullable=True),
        sa.Column("plan_digest", sa.String(length=80), nullable=True),
        sa.Column("plan_artifact_uri", sa.String(length=1000), nullable=True),
        sa.Column("plan_has_changes", sa.Boolean(), nullable=True),
        sa.Column("resource_additions", sa.Integer(), nullable=True),
        sa.Column("resource_changes", sa.Integer(), nullable=True),
        sa.Column("resource_destroys", sa.Integer(), nullable=True),
        sa.Column("quota_usage", sa.JSON(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("callback_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["infrastructure_targets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_infrastructure_runs_status_available_at",
        "infrastructure_runs",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_infrastructure_runs_workspace_created_at",
        "infrastructure_runs",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_infrastructure_runs_environment_created_at",
        "infrastructure_runs",
        ["environment_id", "created_at"],
    )
    op.create_index(
        "uq_infrastructure_runs_active_environment",
        "infrastructure_runs",
        ["environment_id"],
        unique=True,
        sqlite_where=sa.text(f"status IN ({ACTIVE_INFRA_RUNS})"),
        postgresql_where=sa.text(f"status IN ({ACTIVE_INFRA_RUNS})"),
    )

    op.create_table(
        "infrastructure_run_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("infrastructure_run_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("delivery_id", sa.String(length=180), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("from_status", sa.String(length=40), nullable=True),
        sa.Column("to_status", sa.String(length=40), nullable=True),
        sa.Column("actor_type", sa.String(length=40), nullable=False),
        sa.Column("actor_id", sa.String(length=180), nullable=True),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["infrastructure_run_id"], ["infrastructure_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "infrastructure_run_id", "sequence", name="uq_infra_run_events_sequence"
        ),
        sa.UniqueConstraint("infrastructure_run_id", "delivery_id", name="uq_infra_run_delivery"),
    )
    op.create_index(
        "ix_infrastructure_run_events_workspace_created",
        "infrastructure_run_events",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_infrastructure_run_events_workspace_created", table_name="infrastructure_run_events"
    )
    op.drop_table("infrastructure_run_events")
    op.drop_index("uq_infrastructure_runs_active_environment", table_name="infrastructure_runs")
    op.drop_index("ix_infrastructure_runs_environment_created_at", table_name="infrastructure_runs")
    op.drop_index("ix_infrastructure_runs_workspace_created_at", table_name="infrastructure_runs")
    op.drop_index("ix_infrastructure_runs_status_available_at", table_name="infrastructure_runs")
    op.drop_table("infrastructure_runs")
    op.drop_index("ix_infrastructure_targets_workspace_id", table_name="infrastructure_targets")
    op.drop_table("infrastructure_targets")
    op.drop_column("environments", "quota_services")
    op.drop_column("environments", "quota_persistent_volume_claims")
    op.drop_column("environments", "quota_pods")
    op.drop_column("environments", "quota_memory_mib")
    op.drop_column("environments", "quota_cpu_millis")
    op.drop_column("environments", "allow_destroy")
    op.drop_column("environments", "protected")
    op.drop_column("environments", "namespace")
    op.drop_column("environments", "cluster_name")
