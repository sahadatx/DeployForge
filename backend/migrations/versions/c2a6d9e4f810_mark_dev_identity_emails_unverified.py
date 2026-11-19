"""Mark development identity email addresses as unverified.

Revision ID: c2a6d9e4f810
Revises: ea4c91d2b607
Create Date: 2026-11-04 00:00:00.000000
"""

from alembic import op

revision = "c2a6d9e4f810"
down_revision = "ea4c91d2b607"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE users SET email_verified = false "
        "WHERE issuer = 'urn:deployforge:development'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE users SET email_verified = true "
        "WHERE issuer = 'urn:deployforge:development'"
    )
