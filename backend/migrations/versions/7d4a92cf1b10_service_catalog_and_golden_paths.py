"""service catalog teams and golden paths

Revision ID: 7d4a92cf1b10
Revises: 300637d2a933
Create Date: 2026-10-25 11:15:00.000000
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "7d4a92cf1b10"
down_revision = "300637d2a933"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    legacy_service_rows = (
        bind.execute(
            sa.text(
                "SELECT id, workspace_id, owner_team FROM services "
                "WHERE owner_team IS NOT NULL AND trim(owner_team) != ''"
            )
        )
        .mappings()
        .all()
    )

    op.create_table(
        "service_teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_service_teams_workspace_slug"),
    )
    op.create_index("ix_service_teams_workspace_id", "service_teams", ["workspace_id"])
    op.create_table(
        "golden_paths",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("icon", sa.String(length=40), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("config_schema", sa.JSON(), nullable=False),
        sa.Column("defaults", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_golden_paths_slug"),
    )

    with op.batch_alter_table("services") as batch_op:
        batch_op.add_column(sa.Column("system", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("owner_team_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("documentation_url", sa.String(length=500), nullable=True))
        batch_op.add_column(
            sa.Column("criticality", sa.String(length=24), nullable=False, server_default="tier_2")
        )
        batch_op.add_column(
            sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch_op.add_column(
            sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch_op.add_column(sa.Column("golden_path_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("golden_path_snapshot", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("created_by", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch_op.create_foreign_key(
            "fk_services_owner_team_id_service_teams",
            "service_teams",
            ["owner_team_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_services_golden_path_id_golden_paths",
            "golden_paths",
            ["golden_path_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_services_created_by_users",
            "users",
            ["created_by"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_services_owner_team_id", ["owner_team_id"])
        batch_op.create_index("ix_services_lifecycle", ["workspace_id", "lifecycle"])
        batch_op.drop_column("owner_team")

    # Preserve legacy free-form owners by migrating them to workspace teams.
    team_ids: dict[tuple[str, str], str] = {}
    for row in legacy_service_rows:
        workspace_id = str(row["workspace_id"])
        name = row["owner_team"].strip()
        key = (workspace_id, name.casefold())
        if key not in team_ids:
            team_id = uuid.uuid4().hex
            slug_base = "".join(char.lower() if char.isalnum() else "-" for char in name)
            slug_base = "-".join(part for part in slug_base.split("-") if part)[:70] or "team"
            slug = slug_base
            suffix = 2
            while bind.execute(
                sa.text(
                    "SELECT 1 FROM service_teams "
                    "WHERE workspace_id = :workspace_id AND slug = :slug"
                ),
                {"workspace_id": row["workspace_id"], "slug": slug},
            ).first():
                slug = f"{slug_base[:68]}-{suffix}"
                suffix += 1
            now = datetime.now(UTC)
            bind.execute(
                sa.text(
                    "INSERT INTO service_teams "
                    "(id, workspace_id, slug, name, created_at, updated_at) "
                    "VALUES (:id, :workspace_id, :slug, :name, :created_at, :updated_at)"
                ),
                {
                    "id": team_id,
                    "workspace_id": row["workspace_id"],
                    "slug": slug,
                    "name": name,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            team_ids[key] = team_id
        bind.execute(
            sa.text("UPDATE services SET owner_team_id = :team_id WHERE id = :service_id"),
            {"team_id": team_ids[key], "service_id": row["id"]},
        )

    now = datetime.now(UTC)
    paths = [
        {
            "slug": "api-service",
            "name": "API service",
            "description": "An HTTP API with health checks, ownership and secure defaults.",
            "category": "Backend",
            "icon": "braces",
            "capabilities": [
                "Health endpoint",
                "Container-ready",
                "Service ownership",
                "Security baseline",
            ],
            "config_schema": [
                {
                    "key": "runtime",
                    "label": "Runtime",
                    "type": "select",
                    "options": ["Python", "Node.js", "Go", "Java"],
                },
                {
                    "key": "visibility",
                    "label": "Service visibility",
                    "type": "select",
                    "options": ["internal", "public"],
                },
            ],
            "defaults": {"runtime": "Python", "visibility": "internal"},
        },
        {
            "slug": "web-application",
            "name": "Web application",
            "description": "A web frontend with ownership, runtime data and production defaults.",
            "category": "Frontend",
            "icon": "panels-top-left",
            "capabilities": [
                "Web workload",
                "Container-ready",
                "Service ownership",
                "Environment-aware",
            ],
            "config_schema": [
                {
                    "key": "framework",
                    "label": "Framework",
                    "type": "select",
                    "options": ["React", "Next.js", "Vue", "Angular"],
                },
                {
                    "key": "visibility",
                    "label": "Service visibility",
                    "type": "select",
                    "options": ["internal", "public"],
                },
            ],
            "defaults": {"framework": "React", "visibility": "internal"},
        },
        {
            "slug": "background-worker",
            "name": "Background worker",
            "description": "An asynchronous worker with ownership and workload metadata.",
            "category": "Workers",
            "icon": "workflow",
            "capabilities": [
                "Worker workload",
                "Container-ready",
                "Service ownership",
                "Operational metadata",
            ],
            "config_schema": [
                {
                    "key": "runtime",
                    "label": "Runtime",
                    "type": "select",
                    "options": ["Python", "Node.js", "Go", "Java"],
                },
                {"key": "queue", "label": "Queue", "type": "text"},
            ],
            "defaults": {"runtime": "Python", "queue": "default"},
        },
    ]
    golden_path_table = sa.table(
        "golden_paths",
        sa.column("id", sa.Uuid()),
        sa.column("slug", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("category", sa.String()),
        sa.column("icon", sa.String()),
        sa.column("version", sa.String()),
        sa.column("status", sa.String()),
        sa.column("capabilities", sa.JSON()),
        sa.column("config_schema", sa.JSON()),
        sa.column("defaults", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        golden_path_table,
        [
            {
                "id": uuid.uuid4(),
                **path,
                "version": "1.0.0",
                "status": "available",
                "created_at": now,
                "updated_at": now,
            }
            for path in paths
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    legacy_owner_rows = bind.execute(
        sa.text(
            "SELECT services.id AS service_id, service_teams.name AS team_name "
            "FROM services JOIN service_teams ON service_teams.id = services.owner_team_id"
        )
    ).all()
    with op.batch_alter_table("services") as batch_op:
        batch_op.add_column(sa.Column("owner_team", sa.String(length=120), nullable=True))
        batch_op.drop_index("ix_services_lifecycle")
        batch_op.drop_index("ix_services_owner_team_id")
        batch_op.drop_constraint("fk_services_created_by_users", type_="foreignkey")
        batch_op.drop_constraint("fk_services_golden_path_id_golden_paths", type_="foreignkey")
        batch_op.drop_constraint("fk_services_owner_team_id_service_teams", type_="foreignkey")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("created_by")
        batch_op.drop_column("golden_path_snapshot")
        batch_op.drop_column("golden_path_id")
        batch_op.drop_column("metadata")
        batch_op.drop_column("tags")
        batch_op.drop_column("criticality")
        batch_op.drop_column("documentation_url")
        batch_op.drop_column("owner_team_id")
        batch_op.drop_column("system")
    for service_id, team_name in legacy_owner_rows:
        bind.execute(
            sa.text("UPDATE services SET owner_team = :team_name WHERE id = :service_id"),
            {"team_name": team_name, "service_id": service_id},
        )
    op.drop_index("ix_service_teams_workspace_id", table_name="service_teams")
    op.drop_table("service_teams")
    op.drop_table("golden_paths")
