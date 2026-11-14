"""track and revoke OIDC allowlist-managed platform administrator access

Revision ID: a17d3f8c6b20
Revises: 5d2f9b7c1a40
"""

import sqlalchemy as sa
from alembic import op

revision = "a17d3f8c6b20"
down_revision = "5d2f9b7c1a40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "platform_admin_source",
            sa.String(length=32),
            server_default="none",
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE users SET platform_admin_source = "
        "CASE WHEN is_platform_admin THEN 'legacy' ELSE 'none' END"
    )


def downgrade() -> None:
    op.drop_column("users", "platform_admin_source")
