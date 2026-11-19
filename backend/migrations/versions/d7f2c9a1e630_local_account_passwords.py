"""Store password hashes for operator-provisioned development accounts.

Revision ID: d7f2c9a1e630
Revises: c2a6d9e4f810
"""

import sqlalchemy as sa
from alembic import op

revision = "d7f2c9a1e630"
down_revision = "c2a6d9e4f810"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(512), nullable=True))
    # Sessions established by the retired email-only flow must not bypass the
    # newly required password. OIDC sessions and all identity IDs stay intact.
    op.execute(
        "DELETE FROM user_sessions WHERE user_id IN "
        "(SELECT id FROM users WHERE issuer = 'urn:deployforge:development')"
    )


def downgrade() -> None:
    op.drop_column("users", "password_hash")
