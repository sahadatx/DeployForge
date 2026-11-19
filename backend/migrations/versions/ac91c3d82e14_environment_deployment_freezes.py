"""environment deployment freeze windows

Revision ID: ac91c3d82e14
Revises: 87e5ad10c243
Create Date: 2026-11-07 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "ac91c3d82e14"
down_revision = "87e5ad10c243"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "environments", sa.Column("deployment_freeze_start_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "environments", sa.Column("deployment_freeze_until", sa.DateTime(timezone=True))
    )
    op.add_column("environments", sa.Column("deployment_freeze_reason", sa.Text()))


def downgrade() -> None:
    op.drop_column("environments", "deployment_freeze_reason")
    op.drop_column("environments", "deployment_freeze_until")
    op.drop_column("environments", "deployment_freeze_start_at")
