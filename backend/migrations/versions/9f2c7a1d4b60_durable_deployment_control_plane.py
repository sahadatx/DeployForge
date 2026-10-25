"""durable deployment control plane

Revision ID: 9f2c7a1d4b60
Revises: 7d4a92cf1b10
Create Date: 2026-10-25 12:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "9f2c7a1d4b60"
down_revision = "7d4a92cf1b10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("deployments") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=128), nullable=True))
        batch_op.add_column(
            sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False)
        )
        batch_op.add_column(
            sa.Column(
                "available_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("lease_owner", sa.String(length=120), nullable=True))
        batch_op.add_column(
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("approved_by", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("failure_code", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("failure_message", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_deployments_approved_by_users",
            "users",
            ["approved_by"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_deployments_workspace_idempotency", ["workspace_id", "idempotency_key"]
        )
    op.create_index(
        "ix_deployments_status_available_at", "deployments", ["status", "available_at"]
    )

    op.create_table(
        "deployment_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deployment_id", "sequence", name="uq_deployment_events_sequence"),
    )
    op.create_index(
        "ix_deployment_events_workspace_created_at",
        "deployment_events",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_deployment_events_workspace_created_at", table_name="deployment_events")
    op.drop_table("deployment_events")
    op.drop_index("ix_deployments_status_available_at", table_name="deployments")
    with op.batch_alter_table("deployments") as batch_op:
        batch_op.drop_constraint("uq_deployments_workspace_idempotency", type_="unique")
        batch_op.drop_constraint("fk_deployments_approved_by_users", type_="foreignkey")
        for column in (
            "failure_message",
            "failure_code",
            "approved_at",
            "approved_by",
            "finished_at",
            "started_at",
            "lease_expires_at",
            "lease_owner",
            "available_at",
            "max_attempts",
            "attempt_count",
            "idempotency_key",
        ):
            batch_op.drop_column(column)
