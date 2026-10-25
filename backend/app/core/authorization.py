from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.models import User, Workspace, WorkspaceMembership


def require_workspace_access(
    db: Session,
    user: User,
    workspace_id: UUID,
    *,
    roles: set[str] | None = None,
) -> tuple[Workspace, str]:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    if user.is_platform_admin:
        return workspace, "platform_admin"

    membership = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user.id,
        )
    )
    if membership is None:
        record_audit(
            db,
            actor=user,
            action="authorization.workspace_access.denied",
            resource_type="workspace",
            resource_id=workspace_id,
            workspace_id=workspace_id,
            outcome="denied",
            details={"reason": "membership_required"},
        )
        db.commit()
        # Hide the existence of workspaces from users who do not belong to them.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    if roles is not None and membership.role not in roles:
        record_audit(
            db,
            actor=user,
            action="authorization.workspace_action.denied",
            resource_type="workspace",
            resource_id=workspace_id,
            workspace_id=workspace_id,
            outcome="denied",
            details={"reason": "insufficient_role", "required_roles": sorted(roles)},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient workspace role"
        )
    return workspace, membership.role
