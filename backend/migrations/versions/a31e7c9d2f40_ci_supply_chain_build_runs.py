"""CI build runs and supply-chain evidence

Revision ID: a31e7c9d2f40
Revises: 9f2c7a1d4b60
Create Date: 2026-10-25 13:05:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "a31e7c9d2f40"
down_revision = "9f2c7a1d4b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("deployments") as batch_op:
        batch_op.add_column(sa.Column("source_ref", sa.String(length=255), nullable=True))

    op.create_table(
        "build_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("repository_url", sa.String(length=500), nullable=True),
        sa.Column("workflow_path", sa.String(length=255), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_run_id", sa.String(length=120), nullable=True),
        sa.Column("run_url", sa.String(length=1000), nullable=True),
        sa.Column("callback_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "deployment_id", "attempt_number", name="uq_build_runs_deployment_attempt"
        ),
    )
    op.create_index("ix_build_runs_status_available_at", "build_runs", ["status", "available_at"])
    op.create_index(
        "ix_build_runs_workspace_created_at", "build_runs", ["workspace_id", "created_at"]
    )

    op.create_table(
        "build_run_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("build_run_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("delivery_id", sa.String(length=120), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("from_status", sa.String(length=40), nullable=True),
        sa.Column("to_status", sa.String(length=40), nullable=True),
        sa.Column("actor_type", sa.String(length=40), nullable=False),
        sa.Column("actor_id", sa.String(length=180), nullable=True),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["build_run_id"], ["build_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("build_run_id", "sequence", name="uq_build_run_events_sequence"),
        sa.UniqueConstraint("build_run_id", "delivery_id", name="uq_build_run_events_delivery"),
    )
    op.create_index(
        "ix_build_run_events_workspace_created_at",
        "build_run_events",
        ["workspace_id", "created_at"],
    )

    op.create_table(
        "build_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("build_run_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("artifact_type", sa.String(length=40), nullable=False),
        sa.Column("uri", sa.String(length=1000), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
        sa.Column("sbom_format", sa.String(length=40), nullable=False),
        sa.Column("sbom_uri", sa.String(length=1000), nullable=False),
        sa.Column("sbom_digest", sa.String(length=80), nullable=False),
        sa.Column("vulnerability_summary", sa.JSON(), nullable=False),
        sa.Column("signature_uri", sa.String(length=1000), nullable=False),
        sa.Column("signer_identity", sa.String(length=500), nullable=False),
        sa.Column("oidc_issuer", sa.String(length=500), nullable=False),
        sa.Column("provenance_uri", sa.String(length=1000), nullable=False),
        sa.Column("provenance_digest", sa.String(length=80), nullable=False),
        sa.Column("evidence_status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["build_run_id"], ["build_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("build_run_id", "digest", name="uq_build_artifacts_run_digest"),
    )
    op.create_index(
        "ix_build_artifacts_workspace_created_at",
        "build_artifacts",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_build_artifacts_workspace_created_at", table_name="build_artifacts")
    op.drop_table("build_artifacts")
    op.drop_index("ix_build_run_events_workspace_created_at", table_name="build_run_events")
    op.drop_table("build_run_events")
    op.drop_index("ix_build_runs_workspace_created_at", table_name="build_runs")
    op.drop_index("ix_build_runs_status_available_at", table_name="build_runs")
    op.drop_table("build_runs")
    with op.batch_alter_table("deployments") as batch_op:
        batch_op.drop_column("source_ref")
