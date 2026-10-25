"""Kubernetes GitOps targets, sync history, and reconciliation events.

Revision ID: b4e2c8a16d90
Revises: a31e7c9d2f40
Create Date: 2026-10-25 14:20:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "b4e2c8a16d90"
down_revision = "a31e7c9d2f40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gitops_targets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("application_name", sa.String(length=253), nullable=False),
        sa.Column("expected_project", sa.String(length=80), nullable=False),
        sa.Column("expected_cluster_name", sa.String(length=180), nullable=True),
        sa.Column("artifact_name", sa.String(length=180), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "service_id",
            "environment_id",
            name="uq_gitops_targets_service_environment",
        ),
        sa.UniqueConstraint(
            "workspace_id", "application_name", name="uq_gitops_targets_workspace_application"
        ),
    )
    op.create_index("ix_gitops_targets_workspace_id", "gitops_targets", ["workspace_id"])

    op.create_table(
        "gitops_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
        sa.Column("build_run_id", sa.Uuid(), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("rollback_from_run_id", sa.Uuid(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("action_kind", sa.String(length=24), nullable=False),
        sa.Column("application_name", sa.String(length=253), nullable=False),
        sa.Column("desired_revision", sa.String(length=255), nullable=True),
        sa.Column("observed_revision", sa.String(length=255), nullable=True),
        sa.Column("artifact_uri", sa.String(length=1000), nullable=True),
        sa.Column("artifact_digest", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("sync_status", sa.String(length=40), nullable=True),
        sa.Column("health_status", sa.String(length=40), nullable=True),
        sa.Column("operation_phase", sa.String(length=40), nullable=True),
        sa.Column("destination_name", sa.String(length=180), nullable=True),
        sa.Column("destination_server", sa.String(length=500), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["build_run_id"], ["build_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_id"], ["gitops_targets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["rollback_from_run_id"], ["gitops_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "deployment_id", "attempt_number", name="uq_gitops_runs_deployment_attempt"
        ),
    )
    op.create_index("ix_gitops_runs_status_next_poll", "gitops_runs", ["status", "next_poll_at"])
    op.create_index("ix_gitops_runs_target_created_at", "gitops_runs", ["target_id", "created_at"])
    op.create_index(
        "ix_gitops_runs_workspace_created_at", "gitops_runs", ["workspace_id", "created_at"]
    )

    op.create_table(
        "gitops_run_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("gitops_run_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("from_status", sa.String(length=40), nullable=True),
        sa.Column("to_status", sa.String(length=40), nullable=True),
        sa.Column("actor_type", sa.String(length=40), nullable=False),
        sa.Column("actor_id", sa.String(length=180), nullable=True),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["gitops_run_id"], ["gitops_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gitops_run_id", "sequence", name="uq_gitops_run_events_sequence"),
    )
    op.create_index(
        "ix_gitops_run_events_workspace_created_at",
        "gitops_run_events",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_gitops_run_events_workspace_created_at", table_name="gitops_run_events")
    op.drop_table("gitops_run_events")
    op.drop_index("ix_gitops_runs_workspace_created_at", table_name="gitops_runs")
    op.drop_index("ix_gitops_runs_target_created_at", table_name="gitops_runs")
    op.drop_index("ix_gitops_runs_status_next_poll", table_name="gitops_runs")
    op.drop_table("gitops_runs")
    op.drop_index("ix_gitops_targets_workspace_id", table_name="gitops_targets")
    op.drop_table("gitops_targets")
