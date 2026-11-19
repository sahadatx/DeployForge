"""add durable GitHub Golden Path repository provisioning

Revision ID: f6b095e24c18
Revises: e5a84c13d2f7
Create Date: 2026-11-07 20:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "f6b095e24c18"
down_revision = "e5a84c13d2f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_repository_provisionings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("golden_path_slug", sa.String(length=100), nullable=False),
        sa.Column("repository_owner", sa.String(length=100), nullable=False),
        sa.Column("repository_name", sa.String(length=100), nullable=False),
        sa.Column("template_owner", sa.String(length=100), nullable=False),
        sa.Column("template_name", sa.String(length=100), nullable=False),
        sa.Column("is_private", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_repository_id", sa.String(length=80), nullable=True),
        sa.Column("repository_url", sa.String(length=500), nullable=True),
        sa.Column("default_branch", sa.String(length=255), nullable=True),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_github_repository_provisionings_status_available",
        "github_repository_provisionings",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_github_repository_provisionings_workspace_created",
        "github_repository_provisionings",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "uq_github_repository_provisionings_service",
        "github_repository_provisionings",
        ["service_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_github_repository_provisionings_service",
        table_name="github_repository_provisionings",
    )
    op.drop_index(
        "ix_github_repository_provisionings_workspace_created",
        table_name="github_repository_provisionings",
    )
    op.drop_index(
        "ix_github_repository_provisionings_status_available",
        table_name="github_repository_provisionings",
    )
    op.drop_table("github_repository_provisionings")
