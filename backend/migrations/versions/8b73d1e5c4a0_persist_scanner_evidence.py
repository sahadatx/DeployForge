"""persist scanner evidence reference and report metadata

Revision ID: 8b73d1e5c4a0
Revises: ac91c3d82e14
Create Date: 2026-11-07 12:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "8b73d1e5c4a0"
down_revision = "ac91c3d82e14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("build_artifacts", sa.Column("vulnerability_uri", sa.String(length=1000)))
    op.add_column(
        "build_artifacts", sa.Column("vulnerability_report_digest", sa.String(length=80))
    )
    op.add_column(
        "build_artifacts", sa.Column("vulnerability_scanner_version", sa.String(length=100))
    )


def downgrade() -> None:
    op.drop_column("build_artifacts", "vulnerability_scanner_version")
    op.drop_column("build_artifacts", "vulnerability_report_digest")
    op.drop_column("build_artifacts", "vulnerability_uri")
