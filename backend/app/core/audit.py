from uuid import UUID

from sqlalchemy.orm import Session

from app.models import AuditEvent, User


def record_audit(
    db: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: str | UUID | None = None,
    workspace_id: UUID | None = None,
    actor: User | None = None,
    outcome: str = "succeeded",
    details: dict | None = None,
) -> AuditEvent:
    audit_details = dict(details or {})
    if actor is not None:
        audit_details.setdefault("actor_email", actor.email)
        audit_details.setdefault("actor_name", actor.display_name)
    event = AuditEvent(
        workspace_id=workspace_id,
        actor_type="user" if actor else "system",
        actor_id=str(actor.id) if actor else None,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        outcome=outcome,
        details=audit_details,
    )
    db.add(event)
    return event
