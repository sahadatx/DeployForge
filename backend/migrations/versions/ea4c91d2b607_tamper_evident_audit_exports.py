"""Persist cursors and immutable audit export batches.

Revision ID: ea4c91d2b607
Revises: a17d3f8c6b20
"""

import sqlalchemy as sa
from alembic import op

revision = "ea4c91d2b607"
down_revision = "a17d3f8c6b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_export_cursors",
        sa.Column("export_name", sa.String(length=100), nullable=False),
        sa.Column("cursor_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_event_id", sa.Uuid(), nullable=True),
        sa.Column("chain_hash", sa.String(length=64), nullable=False),
        sa.Column("batch_sequence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("export_name"),
    )
    op.create_table(
        "audit_export_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("export_name", sa.String(length=100), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("object_stem", sa.String(length=1024), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("previous_chain_hash", sa.String(length=64), nullable=False),
        sa.Column("final_chain_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["export_name"], ["audit_export_cursors.export_name"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_stem"),
        sa.UniqueConstraint("export_name", "sequence", name="uq_audit_export_batches_sequence"),
    )
    op.create_index(
        "ix_audit_export_batches_status_sequence",
        "audit_export_batches",
        ["status", "sequence"],
    )
    op.create_table(
        "audit_export_batch_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("record", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["audit_events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["audit_export_batches.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint(
            "batch_id", "ordinal", name="uq_audit_export_batch_events_ordinal"
        ),
    )
    op.create_index(
        "ix_audit_export_batch_events_batch_ordinal",
        "audit_export_batch_events",
        ["batch_id", "ordinal"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_audit_export_batch_events_batch_ordinal",
        table_name="audit_export_batch_events",
    )
    op.drop_table("audit_export_batch_events")
    op.drop_index(
        "ix_audit_export_batches_status_sequence", table_name="audit_export_batches"
    )
    op.drop_table("audit_export_batches")
    op.drop_table("audit_export_cursors")
