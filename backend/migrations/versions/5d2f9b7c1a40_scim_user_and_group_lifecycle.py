"""Add SCIM user and group provisioning resources.

Revision ID: 5d2f9b7c1a40
Revises: c937e1a4b60d
"""

import sqlalchemy as sa
from alembic import op

revision = "5d2f9b7c1a40"
down_revision = "c937e1a4b60d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("scim_external_id", sa.String(255), nullable=True))
    with op.batch_alter_table("users") as batch_op:
        batch_op.create_unique_constraint("uq_users_scim_external_id", ["scim_external_id"])

    op.create_table(
        "scim_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("display_name", name="uq_scim_groups_display_name"),
        sa.UniqueConstraint("external_id", name="uq_scim_groups_external_id"),
    )
    op.create_table(
        "scim_group_memberships",
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["scim_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "user_id"),
    )
    op.create_index(
        "ix_scim_group_memberships_user_id", "scim_group_memberships", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_scim_group_memberships_user_id", table_name="scim_group_memberships")
    op.drop_table("scim_group_memberships")
    op.drop_table("scim_groups")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_scim_external_id", type_="unique")
        batch_op.drop_column("scim_external_id")
