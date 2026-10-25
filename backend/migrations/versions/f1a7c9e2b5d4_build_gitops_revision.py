"""Store the manifest revision separately from the source commit.

Revision ID: f1a7c9e2b5d4
Revises: e2f3a1c8d640
Create Date: 2026-10-25 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "f1a7c9e2b5d4"
down_revision = "e2f3a1c8d640"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("build_runs", sa.Column("gitops_revision", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("build_runs", "gitops_revision")
