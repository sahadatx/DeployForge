"""record immutable artifact promotions on deployment requests

Revision ID: b731d4a9ce20
Revises: 8b73d1e5c4a0
Create Date: 2026-11-07 12:45:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "b731d4a9ce20"
down_revision = "8b73d1e5c4a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deployments", sa.Column("promoted_artifact_id", sa.Uuid()))
    op.add_column("deployments", sa.Column("promotion_gitops_revision", sa.String(length=64)))
    op.create_index(
        "ix_deployments_promoted_artifact_id", "deployments", ["promoted_artifact_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_deployments_promoted_artifact_id", table_name="deployments")
    op.drop_column("deployments", "promotion_gitops_revision")
    op.drop_column("deployments", "promoted_artifact_id")
