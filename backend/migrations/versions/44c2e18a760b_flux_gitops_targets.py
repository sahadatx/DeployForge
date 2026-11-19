"""Add provider and reconciliation namespace to GitOps targets."""

import sqlalchemy as sa
from alembic import op

revision = "44c2e18a760b"
down_revision = "f6b095e24c18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gitops_targets",
        sa.Column("provider", sa.String(length=24), server_default="argocd", nullable=False),
    )
    op.add_column(
        "gitops_targets", sa.Column("reconciliation_namespace", sa.String(length=63))
    )


def downgrade() -> None:
    op.drop_column("gitops_targets", "reconciliation_namespace")
    op.drop_column("gitops_targets", "provider")
