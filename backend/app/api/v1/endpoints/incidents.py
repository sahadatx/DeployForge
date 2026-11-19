"""Audited incident response records and independently reviewed postmortems."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_identity, get_db, require_csrf
from app.core.audit import record_audit
from app.core.authorization import require_workspace_access
from app.models import (
    Deployment,
    Environment,
    GitOpsRun,
    GitOpsTarget,
    Incident,
    IncidentEvent,
    IncidentPostmortem,
    Service,
    ServiceLevelObjective,
    SloAlert,
    User,
    UserSession,
    WorkspaceMembership,
)
from app.schemas.core import (
    IncidentAssignment,
    IncidentContract,
    IncidentCreate,
    IncidentEscalation,
    IncidentEventContract,
    IncidentPostmortemContract,
    IncidentPostmortemReview,
    IncidentPostmortemUpsert,
    IncidentTransition,
)

router = APIRouter()
INCIDENT_OPERATOR_ROLES = {"workspace_admin", "platform_engineer", "service_owner"}
TRANSITIONS = {
    "open": {"investigating", "mitigated", "resolved"},
    "investigating": {"mitigated", "resolved"},
    "mitigated": {"investigating", "resolved"},
    "resolved": {"investigating", "closed"},
    "closed": set(),
}


def _load_incident(
    db: Session, workspace_id: UUID, incident_id: UUID, *, lock: bool = False
) -> Incident:
    statement = select(Incident).where(
        Incident.workspace_id == workspace_id,
        Incident.id == incident_id,
    )
    if lock:
        statement = statement.with_for_update()
    incident = db.scalar(statement)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


def _append_event(
    db: Session,
    *,
    incident: Incident,
    actor: User | None,
    event_type: str,
    message: str,
    details: dict | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
) -> IncidentEvent:
    sequence = (
        db.scalar(
            select(func.coalesce(func.max(IncidentEvent.sequence), 0)).where(
                IncidentEvent.incident_id == incident.id
            )
        )
        + 1
    )
    event = IncidentEvent(
        incident_id=incident.id,
        workspace_id=incident.workspace_id,
        sequence=sequence,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        actor_type="user" if actor else "system",
        actor_id=str(actor.id) if actor else None,
        message=message,
        details=details or {},
    )
    db.add(event)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action=f"incident.{event_type}",
        resource_type="incident",
        resource_id=incident.id,
        workspace_id=incident.workspace_id,
        details={"incident_event_id": str(event.id), "sequence": sequence, **(details or {})},
    )
    return event


def _validate_service_environment(
    db: Session,
    workspace_id: UUID,
    service_id: UUID | None,
    environment_id: UUID | None,
) -> None:
    if (
        service_id is not None
        and db.scalar(
            select(Service.id).where(Service.id == service_id, Service.workspace_id == workspace_id)
        )
        is None
    ):
        raise HTTPException(status_code=422, detail="Service is not in this workspace")
    if (
        environment_id is not None
        and db.scalar(
            select(Environment.id).where(
                Environment.id == environment_id,
                Environment.workspace_id == workspace_id,
            )
        )
        is None
    ):
        raise HTTPException(status_code=422, detail="Environment is not in this workspace")


def _source_context(
    db: Session, workspace_id: UUID, source_type: str, source_id: UUID
) -> tuple[UUID | None, UUID | None, str]:
    if source_type == "slo_alert":
        alert = db.scalar(
            select(SloAlert).where(
                SloAlert.id == source_id,
                SloAlert.workspace_id == workspace_id,
                SloAlert.status.in_(("firing", "acknowledged")),
            )
        )
        slo = db.get(ServiceLevelObjective, alert.slo_id) if alert else None
        if alert is None or slo is None:
            raise HTTPException(status_code=404, detail="Active SLO alert not found")
        return slo.service_id, slo.environment_id, alert.summary
    if source_type == "deployment":
        deployment = db.scalar(
            select(Deployment).where(
                Deployment.id == source_id,
                Deployment.workspace_id == workspace_id,
                Deployment.status == "failed",
            )
        )
        if deployment is None:
            raise HTTPException(status_code=404, detail="Failed deployment not found")
        return (
            deployment.service_id,
            deployment.environment_id,
            f"Release {deployment.version} failed",
        )
    if source_type == "gitops":
        run = db.scalar(
            select(GitOpsRun).where(
                GitOpsRun.id == source_id,
                GitOpsRun.workspace_id == workspace_id,
                GitOpsRun.status.in_(("failed", "blocked")),
            )
        )
        if run is None:
            raise HTTPException(status_code=404, detail="Failed GitOps run not found")
        deployment = db.get(Deployment, run.deployment_id)
        target = db.get(GitOpsTarget, run.target_id) if run.target_id else None
        return (
            target.service_id if target else (deployment.service_id if deployment else None),
            target.environment_id
            if target
            else (deployment.environment_id if deployment else None),
            f"{run.application_name} rollout {run.status}",
        )
    raise HTTPException(status_code=422, detail="Unsupported incident source")


def _require_workspace_member(db: Session, workspace_id: UUID, user_id: UUID) -> User:
    member = db.scalar(
        select(WorkspaceMembership.id).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
        )
    )
    user = db.get(User, user_id)
    if member is None or user is None or not user.active:
        raise HTTPException(status_code=422, detail="Assignee must be an active workspace member")
    return user


@router.get(
    "/workspaces/{workspace_id}/incidents",
    response_model=list[IncidentContract],
    summary="List workspace incidents with bounded pagination",
)
def list_incidents(
    workspace_id: UUID,
    incident_status: str | None = Query(default=None, alias="status", max_length=24),
    severity: str | None = Query(default=None, pattern="^sev[0-3]$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100000),
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[Incident]:
    require_workspace_access(db, identity[0], workspace_id)
    query = select(Incident).where(Incident.workspace_id == workspace_id)
    if incident_status:
        if incident_status not in TRANSITIONS and incident_status != "closed":
            raise HTTPException(status_code=422, detail="Unknown incident status")
        query = query.where(Incident.status == incident_status)
    if severity:
        query = query.where(Incident.severity == severity)
    return list(
        db.scalars(
            query.order_by(Incident.updated_at.desc(), Incident.id).limit(limit).offset(offset)
        ).all()
    )


@router.post(
    "/workspaces/{workspace_id}/incidents",
    response_model=IncidentContract,
    status_code=status.HTTP_201_CREATED,
    summary="Create a manual incident or convert an active signal into an incident",
)
def create_incident(
    workspace_id: UUID,
    payload: IncidentCreate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Incident:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=INCIDENT_OPERATOR_ROLES)
    service_id, environment_id = payload.service_id, payload.environment_id
    title = payload.title
    if payload.source_type == "manual":
        _validate_service_environment(db, workspace_id, service_id, environment_id)
    else:
        service_id, environment_id, derived_title = _source_context(
            db, workspace_id, payload.source_type, payload.source_id
        )
        title = payload.title or derived_title
    existing = None
    if payload.source_id is not None:
        existing = db.scalar(
            select(Incident).where(
                Incident.workspace_id == workspace_id,
                Incident.source_type == payload.source_type,
                Incident.source_id == payload.source_id,
            )
        )
    if existing is not None:
        return existing

    incident = Incident(
        workspace_id=workspace_id,
        service_id=service_id,
        environment_id=environment_id,
        source_type=payload.source_type,
        source_id=payload.source_id,
        title=title,
        description=payload.description,
        severity=payload.severity,
        opened_by=actor.id,
    )
    db.add(incident)
    try:
        db.flush()
        _append_event(
            db,
            incident=incident,
            actor=actor,
            event_type="created",
            message="Incident created from manual intake"
            if payload.source_type == "manual"
            else f"Incident created from {payload.source_type} signal",
            details={
                "source_type": payload.source_type,
                "source_id": str(payload.source_id) if payload.source_id else None,
            },
            to_status="open",
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if payload.source_id is not None:
            existing = db.scalar(
                select(Incident).where(
                    Incident.workspace_id == workspace_id,
                    Incident.source_type == payload.source_type,
                    Incident.source_id == payload.source_id,
                )
            )
            if existing is not None:
                return existing
        raise HTTPException(
            status_code=409, detail="Incident could not be created due to a concurrent update"
        ) from exc
    db.refresh(incident)
    return incident


@router.get(
    "/workspaces/{workspace_id}/incidents/{incident_id}",
    response_model=IncidentContract,
    summary="Read an incident record",
)
def get_incident(
    workspace_id: UUID,
    incident_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> Incident:
    require_workspace_access(db, identity[0], workspace_id)
    return _load_incident(db, workspace_id, incident_id)


@router.get(
    "/workspaces/{workspace_id}/incidents/{incident_id}/events",
    response_model=list[IncidentEventContract],
    summary="Read the append-only incident response timeline",
)
def list_incident_events(
    workspace_id: UUID,
    incident_id: UUID,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100000),
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[IncidentEvent]:
    require_workspace_access(db, identity[0], workspace_id)
    incident = _load_incident(db, workspace_id, incident_id)
    return list(
        db.scalars(
            select(IncidentEvent)
            .where(IncidentEvent.incident_id == incident.id)
            .order_by(IncidentEvent.sequence)
            .limit(limit)
            .offset(offset)
        ).all()
    )


@router.post(
    "/workspaces/{workspace_id}/incidents/{incident_id}/assign",
    response_model=IncidentContract,
    summary="Assign or unassign an incident responder",
)
def assign_incident(
    workspace_id: UUID,
    incident_id: UUID,
    payload: IncidentAssignment,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Incident:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=INCIDENT_OPERATOR_ROLES)
    incident = _load_incident(db, workspace_id, incident_id, lock=True)
    if incident.status in {"resolved", "closed"}:
        raise HTTPException(status_code=409, detail="A resolved incident cannot be reassigned")
    if payload.assignee_id is not None:
        _require_workspace_member(db, workspace_id, payload.assignee_id)
    previous = incident.assigned_to
    incident.assigned_to = payload.assignee_id
    _append_event(
        db,
        incident=incident,
        actor=actor,
        event_type="assigned",
        message=payload.comment
        or ("Responder assigned" if payload.assignee_id else "Responder unassigned"),
        details={
            "previous_assignee_id": str(previous) if previous else None,
            "assignee_id": str(payload.assignee_id) if payload.assignee_id else None,
        },
    )
    db.commit()
    db.refresh(incident)
    return incident


@router.post(
    "/workspaces/{workspace_id}/incidents/{incident_id}/escalate",
    response_model=IncidentContract,
    summary="Record escalation level and optional next escalation time",
)
def escalate_incident(
    workspace_id: UUID,
    incident_id: UUID,
    payload: IncidentEscalation,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Incident:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=INCIDENT_OPERATOR_ROLES)
    incident = _load_incident(db, workspace_id, incident_id, lock=True)
    if incident.status in {"resolved", "closed"}:
        raise HTTPException(status_code=409, detail="A resolved incident cannot be escalated")
    if payload.assignee_id is not None:
        _require_workspace_member(db, workspace_id, payload.assignee_id)
    previous = incident.assigned_to
    incident.escalation_level += 1
    if payload.assignee_id is not None:
        incident.assigned_to = payload.assignee_id
    incident.next_escalation_at = payload.next_escalation_at
    _append_event(
        db,
        incident=incident,
        actor=actor,
        event_type="escalated",
        message=payload.comment,
        details={
            "level": incident.escalation_level,
            "previous_assignee_id": str(previous) if previous else None,
            "assignee_id": str(incident.assigned_to) if incident.assigned_to else None,
            "next_escalation_at": incident.next_escalation_at.isoformat()
            if incident.next_escalation_at
            else None,
            "paging_owner": "PagerDuty escalation policy"
            if incident.pagerduty_triggered_at
            else "workspace response workflow",
        },
    )
    db.commit()
    db.refresh(incident)
    return incident


@router.post(
    "/workspaces/{workspace_id}/incidents/{incident_id}/transition",
    response_model=IncidentContract,
    summary="Advance or reopen the incident lifecycle",
)
def transition_incident(
    workspace_id: UUID,
    incident_id: UUID,
    payload: IncidentTransition,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Incident:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=INCIDENT_OPERATOR_ROLES)
    incident = _load_incident(db, workspace_id, incident_id, lock=True)
    old_status = incident.status
    if payload.status not in TRANSITIONS.get(old_status, set()):
        raise HTTPException(
            status_code=409,
            detail=f"Incident cannot transition from {old_status} to {payload.status}",
        )
    if payload.status == "resolved" and not (payload.resolution or "").strip():
        raise HTTPException(status_code=422, detail="A resolution summary is required")
    now = datetime.now(UTC)
    incident.status = payload.status
    if payload.status == "mitigated":
        incident.mitigated_at = now
    elif payload.status == "resolved":
        incident.resolution = payload.resolution.strip()
        incident.resolved_at = now
        incident.closed_at = None
    elif payload.status == "closed":
        incident.closed_at = now
    elif payload.status == "investigating" and old_status == "resolved":
        incident.resolved_at = None
        incident.closed_at = None
        incident.resolution = None
    _append_event(
        db,
        incident=incident,
        actor=actor,
        event_type="status_changed",
        message=payload.comment,
        details={"resolution": incident.resolution} if payload.status == "resolved" else {},
        from_status=old_status,
        to_status=payload.status,
    )
    db.commit()
    db.refresh(incident)
    return incident


@router.get(
    "/workspaces/{workspace_id}/incidents/{incident_id}/postmortem",
    response_model=IncidentPostmortemContract | None,
    summary="Read the incident postmortem draft or published report",
)
def get_postmortem(
    workspace_id: UUID,
    incident_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> IncidentPostmortem | None:
    require_workspace_access(db, identity[0], workspace_id)
    incident = _load_incident(db, workspace_id, incident_id)
    return db.scalar(
        select(IncidentPostmortem).where(IncidentPostmortem.incident_id == incident.id)
    )


@router.put(
    "/workspaces/{workspace_id}/incidents/{incident_id}/postmortem",
    response_model=IncidentPostmortemContract,
    summary="Create or update an incident postmortem draft",
)
def upsert_postmortem(
    workspace_id: UUID,
    incident_id: UUID,
    payload: IncidentPostmortemUpsert,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> IncidentPostmortem:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=INCIDENT_OPERATOR_ROLES)
    incident = _load_incident(db, workspace_id, incident_id, lock=True)
    if incident.status not in {"resolved", "closed"}:
        raise HTTPException(
            status_code=409, detail="Postmortems can be drafted after incident resolution"
        )
    postmortem = db.scalar(
        select(IncidentPostmortem).where(IncidentPostmortem.incident_id == incident.id)
    )
    if postmortem is not None and postmortem.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft postmortem can be edited")
    if postmortem is None:
        postmortem = IncidentPostmortem(
            incident_id=incident.id,
            workspace_id=workspace_id,
            author_id=actor.id,
            status="draft",
            title=payload.title,
            summary=payload.summary,
            impact=payload.impact,
            root_cause=payload.root_cause,
            detection=payload.detection,
            response=payload.response,
            corrective_actions=payload.corrective_actions,
            due_at=payload.due_at,
        )
        db.add(postmortem)
    else:
        if postmortem.author_id != actor.id:
            raise HTTPException(
                status_code=403, detail="Only the postmortem author can update this draft"
            )
        for field in (
            "title",
            "summary",
            "impact",
            "root_cause",
            "detection",
            "response",
            "corrective_actions",
            "due_at",
        ):
            setattr(postmortem, field, getattr(payload, field))
    _append_event(
        db,
        incident=incident,
        actor=actor,
        event_type="postmortem_saved",
        message="Postmortem draft saved",
    )
    db.commit()
    db.refresh(postmortem)
    return postmortem


@router.post(
    "/workspaces/{workspace_id}/incidents/{incident_id}/postmortem/submit",
    response_model=IncidentPostmortemContract,
    summary="Submit a completed postmortem for independent review",
)
def submit_postmortem(
    workspace_id: UUID,
    incident_id: UUID,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> IncidentPostmortem:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=INCIDENT_OPERATOR_ROLES)
    incident = _load_incident(db, workspace_id, incident_id, lock=True)
    postmortem = db.scalar(
        select(IncidentPostmortem).where(IncidentPostmortem.incident_id == incident.id)
    )
    if incident.status not in {"resolved", "closed"} or postmortem is None:
        raise HTTPException(
            status_code=409,
            detail="Resolve the incident and save a postmortem draft before submitting",
        )
    if postmortem.author_id != actor.id or postmortem.status != "draft":
        raise HTTPException(
            status_code=409, detail="Only the draft author can submit a draft for review"
        )
    postmortem.status = "in_review"
    postmortem.submitted_at = datetime.now(UTC)
    postmortem.reviewer_id = None
    postmortem.review_comment = None
    _append_event(
        db,
        incident=incident,
        actor=actor,
        event_type="postmortem_submitted",
        message="Postmortem submitted for independent review",
    )
    db.commit()
    db.refresh(postmortem)
    return postmortem


@router.post(
    "/workspaces/{workspace_id}/incidents/{incident_id}/postmortem/review",
    response_model=IncidentPostmortemContract,
    summary="Approve or return a postmortem for revision",
)
def review_postmortem(
    workspace_id: UUID,
    incident_id: UUID,
    payload: IncidentPostmortemReview,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> IncidentPostmortem:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=INCIDENT_OPERATOR_ROLES)
    incident = _load_incident(db, workspace_id, incident_id, lock=True)
    postmortem = db.scalar(
        select(IncidentPostmortem).where(IncidentPostmortem.incident_id == incident.id)
    )
    if postmortem is None or postmortem.status != "in_review":
        raise HTTPException(status_code=409, detail="No postmortem is awaiting review")
    if postmortem.author_id == actor.id:
        raise HTTPException(
            status_code=403, detail="Postmortem authors cannot review their own report"
        )
    postmortem.reviewer_id = actor.id
    postmortem.review_comment = payload.comment
    if payload.decision == "approve":
        postmortem.status = "published"
        postmortem.published_at = datetime.now(UTC)
    else:
        postmortem.status = "draft"
        postmortem.submitted_at = None
    _append_event(
        db,
        incident=incident,
        actor=actor,
        event_type="postmortem_reviewed",
        message=payload.comment,
        details={"decision": payload.decision, "postmortem_id": str(postmortem.id)},
    )
    db.commit()
    db.refresh(postmortem)
    return postmortem
