"""Telemetry ingestion, service level objectives, and alert lifecycle.

Revision ID: e2f3a1c8d640
Revises: d9e5b7a4c2f1
Create Date: 2026-10-25 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "e2f3a1c8d640"
down_revision = "d9e5b7a4c2f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telemetry_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_telemetry_sources_token_hash"),
    )
    op.create_index(
        "ix_telemetry_sources_workspace_enabled", "telemetry_sources", ["workspace_id", "enabled"]
    )

    op.create_table(
        "telemetry_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("latency_bucket_counts", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["telemetry_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id", "idempotency_key", name="uq_telemetry_samples_source_idempotency"
        ),
        sa.UniqueConstraint(
            "source_id", "observed_at", name="uq_telemetry_samples_source_interval_end"
        ),
    )
    op.create_index(
        "ix_telemetry_samples_service_observed", "telemetry_samples", ["service_id", "observed_at"]
    )
    op.create_index(
        "ix_telemetry_samples_workspace_observed",
        "telemetry_samples",
        ["workspace_id", "observed_at"],
    )

    op.create_table(
        "service_level_objectives",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("indicator", sa.String(length=24), nullable=False),
        sa.Column("objective", sa.Float(), nullable=False),
        sa.Column("window_hours", sa.Integer(), nullable=False),
        sa.Column("latency_threshold_ms", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_slos_workspace_slug"),
    )
    op.create_index(
        "ix_slos_workspace_enabled", "service_level_objectives", ["workspace_id", "enabled"]
    )

    op.create_table(
        "slo_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("slo_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_events", sa.Integer(), nullable=False),
        sa.Column("good_events", sa.Integer(), nullable=False),
        sa.Column("attainment", sa.Float(), nullable=True),
        sa.Column("error_budget_remaining", sa.Float(), nullable=True),
        sa.Column("burn_rate", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["slo_id"], ["service_level_objectives.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_slo_evaluations_slo_created", "slo_evaluations", ["slo_id", "created_at"])
    op.create_index(
        "ix_slo_evaluations_workspace_created", "slo_evaluations", ["workspace_id", "created_at"]
    )

    op.create_table(
        "slo_alerts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("slo_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("acknowledged_by", sa.Uuid(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["slo_id"], ["service_level_objectives.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evaluation_id"], ["slo_evaluations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["acknowledged_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_slo_alerts_workspace_status_created",
        "slo_alerts",
        ["workspace_id", "status", "created_at"],
    )
    op.create_index("ix_slo_alerts_slo_status", "slo_alerts", ["slo_id", "status"])
    op.create_index(
        "uq_slo_alerts_active_per_slo",
        "slo_alerts",
        ["slo_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('firing', 'acknowledged')"),
        postgresql_where=sa.text("status IN ('firing', 'acknowledged')"),
    )


def downgrade() -> None:
    op.drop_index("uq_slo_alerts_active_per_slo", table_name="slo_alerts")
    op.drop_index("ix_slo_alerts_slo_status", table_name="slo_alerts")
    op.drop_index("ix_slo_alerts_workspace_status_created", table_name="slo_alerts")
    op.drop_table("slo_alerts")
    op.drop_index("ix_slo_evaluations_workspace_created", table_name="slo_evaluations")
    op.drop_index("ix_slo_evaluations_slo_created", table_name="slo_evaluations")
    op.drop_table("slo_evaluations")
    op.drop_index("ix_slos_workspace_enabled", table_name="service_level_objectives")
    op.drop_table("service_level_objectives")
    op.drop_index("ix_telemetry_samples_workspace_observed", table_name="telemetry_samples")
    op.drop_index("ix_telemetry_samples_service_observed", table_name="telemetry_samples")
    op.drop_table("telemetry_samples")
    op.drop_index("ix_telemetry_sources_workspace_enabled", table_name="telemetry_sources")
    op.drop_table("telemetry_sources")
