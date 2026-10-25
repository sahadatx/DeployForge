"""Versioned policy-as-code, immutable evaluations, and scoped exceptions.

Revision ID: d9e5b7a4c2f1
Revises: c6d4f9b2a710
Create Date: 2026-10-26 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "d9e5b7a4c2f1"
down_revision = "c6d4f9b2a710"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("enforcement_mode", sa.String(length=16), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_policies_workspace_slug"),
    )
    op.create_index("ix_policies_workspace_enabled", "policies", ["workspace_id", "enabled"])

    op.create_table(
        "policy_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("subject_type", sa.String(length=40), nullable=False),
        sa.Column("subject_id", sa.String(length=180), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("policy_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_policy_evaluations_workspace_created",
        "policy_evaluations",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_policy_evaluations_subject", "policy_evaluations", ["subject_type", "subject_id"]
    )

    op.create_table(
        "policy_exceptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(length=100), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=True),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("review_comment", sa.String(length=1000), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["policy_id"], ["policies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_policy_exceptions_workspace_status", "policy_exceptions", ["workspace_id", "status"]
    )
    op.create_index(
        "ix_policy_exceptions_policy_rule_status",
        "policy_exceptions",
        ["policy_id", "rule_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_policy_exceptions_policy_rule_status", table_name="policy_exceptions")
    op.drop_index("ix_policy_exceptions_workspace_status", table_name="policy_exceptions")
    op.drop_table("policy_exceptions")
    op.drop_index("ix_policy_evaluations_subject", table_name="policy_evaluations")
    op.drop_index("ix_policy_evaluations_workspace_created", table_name="policy_evaluations")
    op.drop_table("policy_evaluations")
    op.drop_index("ix_policies_workspace_enabled", table_name="policies")
    op.drop_table("policies")
