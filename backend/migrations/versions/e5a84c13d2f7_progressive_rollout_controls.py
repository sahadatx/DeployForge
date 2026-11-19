"""persist Argo Rollouts stage evidence and manual control queue

Revision ID: e5a84c13d2f7
Revises: c84f10d2a936
Create Date: 2026-11-07 18:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "e5a84c13d2f7"
down_revision = "c84f10d2a936"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gitops_runs", sa.Column("rollout_snapshot", sa.JSON(), nullable=True))
    op.add_column(
        "gitops_runs", sa.Column("rollout_updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "gitops_rollout_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("gitops_run_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.String(length=1000), nullable=False),
        sa.Column("requested_step", sa.String(length=180), nullable=True),
        sa.Column("requested_rollout_status", sa.String(length=40), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["gitops_run_id"], ["gitops_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_gitops_rollout_actions_status_available",
        "gitops_rollout_actions",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_gitops_rollout_actions_workspace_created",
        "gitops_rollout_actions",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "uq_gitops_rollout_actions_active_run",
        "gitops_rollout_actions",
        ["gitops_run_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued', 'dispatching')"),
        postgresql_where=sa.text("status IN ('queued', 'dispatching')"),
    )


def downgrade() -> None:
    op.drop_index("uq_gitops_rollout_actions_active_run", table_name="gitops_rollout_actions")
    op.drop_index(
        "ix_gitops_rollout_actions_workspace_created", table_name="gitops_rollout_actions"
    )
    op.drop_index("ix_gitops_rollout_actions_status_available", table_name="gitops_rollout_actions")
    op.drop_table("gitops_rollout_actions")
    op.drop_column("gitops_runs", "rollout_updated_at")
    op.drop_column("gitops_runs", "rollout_snapshot")
