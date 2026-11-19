"""Track durable PagerDuty delivery attempts for SLO alerts.

Revision ID: 87e5ad10c243
Revises: d7f2c9a1e630
"""

import sqlalchemy as sa
from alembic import op

revision = "87e5ad10c243"
down_revision = "d7f2c9a1e630"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "slo_alerts", sa.Column("pagerduty_triggered_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "slo_alerts",
        sa.Column("pagerduty_resolved_notified_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "slo_alerts", sa.Column("pagerduty_last_attempt_at", sa.DateTime(timezone=True))
    )


def downgrade() -> None:
    op.drop_column("slo_alerts", "pagerduty_last_attempt_at")
    op.drop_column("slo_alerts", "pagerduty_resolved_notified_at")
    op.drop_column("slo_alerts", "pagerduty_triggered_at")
