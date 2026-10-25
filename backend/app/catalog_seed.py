from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GoldenPath

BUILTIN_GOLDEN_PATHS = [
    {
        "slug": "api-service",
        "name": "API service",
        "description": "A production API with health checks, ownership and secure defaults.",
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
        "description": "A deployable web frontend with ownership and production-minded defaults.",
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
        "description": "An asynchronous worker with clear ownership and workload metadata.",
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


def seed_builtin_golden_paths(db: Session) -> None:
    existing = set(db.scalars(select(GoldenPath.slug)).all())
    for values in BUILTIN_GOLDEN_PATHS:
        if values["slug"] not in existing:
            db.add(GoldenPath(version="1.0.0", status="available", **values))
    db.commit()
