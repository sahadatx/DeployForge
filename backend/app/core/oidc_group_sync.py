"""Synchronize OIDC group claims into workspace-scoped memberships."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.models import User, Workspace, WorkspaceMembership

ROLE_PRIORITY = {
    "auditor": 0,
    "service_owner": 1,
    "platform_engineer": 2,
    "workspace_admin": 3,
}


def sync_oidc_group_memberships(db: Session, user: User, group_names: list[str]) -> None:
    """Apply mapped roles, retaining manual memberships and revoking stale IdP grants."""
    mappings = settings.oidc_group_role_map
    normalized_by_key: dict[str, str] = {}
    for name in sorted(group_names, key=lambda value: (value.casefold(), value)):
        normalized = name.strip()
        if normalized:
            normalized_by_key.setdefault(normalized.casefold(), normalized)

    desired: dict[str, dict[str, object]] = {}
    for key in sorted(normalized_by_key):
        for slug, role in mappings.get(key, {}).items():
            assignment = desired.setdefault(slug, {"role": role, "groups": []})
            if ROLE_PRIORITY[role] > ROLE_PRIORITY[str(assignment["role"])]:
                assignment["role"] = role
            assignment["groups"].append(normalized_by_key[key])

    workspaces = (
        db.scalars(select(Workspace).where(Workspace.slug.in_(desired))).all()
        if desired
        else []
    )
    workspace_by_slug = {workspace.slug: workspace for workspace in workspaces}
    missing = set(desired) - set(workspace_by_slug)
    if missing:
        raise ValueError(
            "An identity group maps to an unknown DeployForge workspace: "
            + ", ".join(sorted(missing))
        )

    existing = {
        membership.workspace_id: membership
        for membership in db.scalars(
            select(WorkspaceMembership).where(
                WorkspaceMembership.user_id == user.id,
                WorkspaceMembership.identity_source == "idp",
            )
        ).all()
    }
    desired_ids = {workspace_by_slug[slug].id for slug in desired}

    for workspace_id, membership in existing.items():
        if workspace_id in desired_ids:
            continue
        record_audit(
            db,
            actor=user,
            action="identity.membership.deprovisioned",
            resource_type="workspace_membership",
            resource_id=membership.id,
            workspace_id=workspace_id,
            details={"identity_source": "idp", "previous_role": membership.role},
        )
        db.delete(membership)

    for slug, assignment in desired.items():
        workspace = workspace_by_slug[slug]
        manual = db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace.id,
                WorkspaceMembership.user_id == user.id,
                WorkspaceMembership.identity_source != "idp",
            )
        )
        if manual is not None:
            continue

        groups = sorted(set(assignment["groups"]), key=lambda value: (value.casefold(), value))
        role = str(assignment["role"])
        membership = existing.get(workspace.id)
        if membership is None:
            membership = WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=role,
                identity_source="idp",
                identity_groups=groups,
            )
            db.add(membership)
            db.flush()
            record_audit(
                db,
                actor=user,
                action="identity.membership.provisioned",
                resource_type="workspace_membership",
                resource_id=membership.id,
                workspace_id=workspace.id,
                details={"identity_source": "idp", "role": role, "groups": groups},
            )
        elif membership.role != role or sorted(membership.identity_groups or []) != groups:
            previous_role = membership.role
            membership.role = role
            membership.identity_groups = groups
            record_audit(
                db,
                actor=user,
                action="identity.membership.updated",
                resource_type="workspace_membership",
                resource_id=membership.id,
                workspace_id=workspace.id,
                details={
                    "identity_source": "idp",
                    "previous_role": previous_role,
                    "role": role,
                    "groups": groups,
                },
            )
