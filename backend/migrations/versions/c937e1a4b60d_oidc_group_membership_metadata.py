"""Track identity-provider managed workspace memberships.

Revision ID: c937e1a4b60d
Revises: f1a7c9e2b5d4
"""

import sqlalchemy as sa
from alembic import op

revision = "c937e1a4b60d"
down_revision = "f1a7c9e2b5d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_memberships",
        sa.Column("identity_source", sa.String(40), server_default="manual", nullable=False),
    )
    op.add_column(
        "workspace_memberships",
        sa.Column("identity_groups", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("workspace_memberships", "identity_groups")
    op.drop_column("workspace_memberships", "identity_source")
