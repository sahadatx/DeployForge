from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_identity, get_db
from app.core.authorization import require_workspace_access
from app.models import AuditEvent, User, UserSession
from app.schemas.core import AuditEventContract

router = APIRouter()


@router.get("", response_model=list[AuditEventContract], summary="Read audit events")
def list_audit_events(
    workspace_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[AuditEvent]:
    user = identity[0]
    query = select(AuditEvent)
    if user.is_platform_admin:
        if workspace_id is not None:
            query = query.where(AuditEvent.workspace_id == workspace_id)
    else:
        if workspace_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="workspace_id is required for workspace-scoped audit access",
            )
        require_workspace_access(db, user, workspace_id)
        query = query.where(AuditEvent.workspace_id == workspace_id)
    return list(db.scalars(query.order_by(AuditEvent.created_at.desc()).limit(limit)).all())
