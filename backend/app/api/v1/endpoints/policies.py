from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_identity, get_db, require_csrf
from app.core.audit import record_audit
from app.core.authorization import require_workspace_access
from app.core.policies import POLICY_ADMIN_ROLES, evaluate_policy_set
from app.models import (
    Environment,
    Policy,
    PolicyEvaluation,
    PolicyException,
    Service,
    User,
    UserSession,
)
from app.schemas.core import (
    PolicyContract,
    PolicyCreate,
    PolicyEvaluationContract,
    PolicyEvaluationRequest,
    PolicyExceptionContract,
    PolicyExceptionCreate,
    PolicyExceptionDecision,
    PolicyPatch,
)

router = APIRouter()


def _load_policy(db: Session, workspace_id: UUID, policy_id: UUID) -> Policy:
    policy = db.scalar(
        select(Policy).where(Policy.workspace_id == workspace_id, Policy.id == policy_id)
    )
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


def _load_exception(db: Session, workspace_id: UUID, exception_id: UUID) -> PolicyException:
    exception = db.scalar(
        select(PolicyException).where(
            PolicyException.workspace_id == workspace_id,
            PolicyException.id == exception_id,
        )
    )
    if exception is None:
        raise HTTPException(status_code=404, detail="Policy exception not found")
    return exception


@router.get(
    "/workspaces/{workspace_id}/policies",
    response_model=list[PolicyContract],
    summary="List workspace policies",
)
def list_policies(
    workspace_id: UUID,
    include_disabled: bool = False,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[Policy]:
    require_workspace_access(db, identity[0], workspace_id)
    query = select(Policy).where(Policy.workspace_id == workspace_id)
    if not include_disabled:
        query = query.where(Policy.enabled.is_(True))
    return list(db.scalars(query.order_by(Policy.slug, Policy.id)).all())


@router.post(
    "/workspaces/{workspace_id}/policies",
    response_model=PolicyContract,
    status_code=status.HTTP_201_CREATED,
    summary="Create a versioned workspace policy",
)
def create_policy(
    workspace_id: UUID,
    payload: PolicyCreate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Policy:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=POLICY_ADMIN_ROLES)
    policy = Policy(
        workspace_id=workspace_id,
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        enabled=payload.enabled,
        enforcement_mode=payload.enforcement_mode,
        actions=payload.actions,
        rules=[rule.model_dump(mode="json", exclude_unset=True) for rule in payload.rules],
        version=1,
        created_by=actor.id,
        updated_by=actor.id,
    )
    db.add(policy)
    try:
        db.flush()
        record_audit(
            db,
            actor=actor,
            action="policy.created",
            resource_type="policy",
            resource_id=policy.id,
            workspace_id=workspace_id,
            details={
                "slug": policy.slug,
                "version": policy.version,
                "actions": policy.actions,
                "enforcement_mode": policy.enforcement_mode,
                "rule_count": len(policy.rules),
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Policy slug already exists in this workspace"
        ) from exc
    db.refresh(policy)
    return policy


@router.get(
    "/workspaces/{workspace_id}/policies/{policy_id}",
    response_model=PolicyContract,
    summary="Read a policy and its current version",
)
def get_policy(
    workspace_id: UUID,
    policy_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> Policy:
    require_workspace_access(db, identity[0], workspace_id)
    return _load_policy(db, workspace_id, policy_id)


@router.patch(
    "/workspaces/{workspace_id}/policies/{policy_id}",
    response_model=PolicyContract,
    summary="Update a policy with optimistic version checking",
)
def update_policy(
    workspace_id: UUID,
    policy_id: UUID,
    payload: PolicyPatch,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Policy:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=POLICY_ADMIN_ROLES)
    policy = _load_policy(db, workspace_id, policy_id)
    if policy.version != payload.expected_version:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Policy changed since it was read",
                "current_version": policy.version,
            },
        )
    updates = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    if not updates:
        return policy
    candidate = {
        "slug": policy.slug,
        "name": updates.get("name", policy.name),
        "description": updates.get("description", policy.description),
        "enabled": updates.get("enabled", policy.enabled),
        "enforcement_mode": updates.get("enforcement_mode", policy.enforcement_mode),
        "actions": updates.get("actions", policy.actions),
        "rules": updates.get("rules", policy.rules),
    }
    try:
        validated = PolicyCreate.model_validate(candidate)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    values = {
        "name": validated.name,
        "description": validated.description,
        "enabled": validated.enabled,
        "enforcement_mode": validated.enforcement_mode,
        "actions": validated.actions,
        "rules": [rule.model_dump(mode="json", exclude_unset=True) for rule in validated.rules],
        "updated_by": actor.id,
        "updated_at": datetime.now(UTC),
        "version": policy.version + 1,
    }
    result = db.execute(
        update(Policy)
        .where(
            Policy.id == policy.id,
            Policy.workspace_id == workspace_id,
            Policy.version == payload.expected_version,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Policy was updated by another operator")
    record_audit(
        db,
        actor=actor,
        action="policy.updated",
        resource_type="policy",
        resource_id=policy.id,
        workspace_id=workspace_id,
        details={
            "previous_version": payload.expected_version,
            "version": values["version"],
            "enabled": validated.enabled,
            "enforcement_mode": validated.enforcement_mode,
            "actions": validated.actions,
            "rule_count": len(validated.rules),
        },
    )
    db.commit()
    db.refresh(policy)
    return policy


@router.post(
    "/workspaces/{workspace_id}/policy-evaluations/evaluate",
    response_model=PolicyEvaluationContract,
    status_code=status.HTTP_201_CREATED,
    summary="Evaluate a policy set against a safe, typed context",
)
def evaluate_policy_endpoint(
    workspace_id: UUID,
    payload: PolicyEvaluationRequest,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> PolicyEvaluation:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id)
    service = None
    environment = None
    if payload.service_id is not None:
        service = db.scalar(
            select(Service).where(
                Service.workspace_id == workspace_id,
                Service.id == payload.service_id,
            )
        )
        if service is None:
            raise HTTPException(status_code=422, detail="Service must belong to this workspace")
    if payload.environment_id is not None:
        environment = db.scalar(
            select(Environment).where(
                Environment.workspace_id == workspace_id,
                Environment.id == payload.environment_id,
            )
        )
        if environment is None:
            raise HTTPException(status_code=422, detail="Environment must belong to this workspace")
    if payload.action == "deployment" and service is None and payload.context.service is None:
        raise HTTPException(
            status_code=422, detail="Deployment evaluation requires service context"
        )
    if payload.context.model_dump(exclude_none=True, mode="json"):
        context = payload.context.model_dump(exclude_none=True, mode="json")
    else:
        context = {}
    if service is not None:
        context["service"] = {
            "slug": service.slug,
            "lifecycle": service.lifecycle,
            "criticality": service.criticality,
            "tags": service.tags,
            "repository_url": service.repository_url,
        }
    if environment is not None:
        context["environment"] = {
            "slug": environment.slug,
            "type": environment.environment_type,
            "protected": environment.protected,
        }
    evaluation = evaluate_policy_set(
        db,
        workspace_id=workspace_id,
        action=payload.action,
        context=context,
        subject_type="policy_simulation",
        subject_id=uuid4(),
        service_id=payload.service_id,
        environment_id=payload.environment_id,
        actor=actor,
    )
    db.commit()
    db.refresh(evaluation)
    return evaluation


@router.get(
    "/workspaces/{workspace_id}/policy-evaluations",
    response_model=list[PolicyEvaluationContract],
    summary="List immutable policy evaluation results",
)
def list_policy_evaluations(
    workspace_id: UUID,
    action: str | None = Query(default=None, max_length=32),
    subject_type: str | None = Query(default=None, max_length=40),
    subject_id: str | None = Query(default=None, max_length=180),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[PolicyEvaluation]:
    require_workspace_access(db, identity[0], workspace_id)
    query = select(PolicyEvaluation).where(PolicyEvaluation.workspace_id == workspace_id)
    if action:
        query = query.where(PolicyEvaluation.action == action)
    if subject_type:
        query = query.where(PolicyEvaluation.subject_type == subject_type)
    if subject_id:
        query = query.where(PolicyEvaluation.subject_id == subject_id)
    return list(
        db.scalars(
            query.order_by(PolicyEvaluation.created_at.desc(), PolicyEvaluation.id)
            .limit(limit)
            .offset(offset)
        ).all()
    )


@router.get(
    "/workspaces/{workspace_id}/policy-evaluations/{evaluation_id}",
    response_model=PolicyEvaluationContract,
    summary="Read a recorded policy evaluation",
)
def get_policy_evaluation(
    workspace_id: UUID,
    evaluation_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> PolicyEvaluation:
    require_workspace_access(db, identity[0], workspace_id)
    evaluation = db.scalar(
        select(PolicyEvaluation).where(
            PolicyEvaluation.workspace_id == workspace_id,
            PolicyEvaluation.id == evaluation_id,
        )
    )
    if evaluation is None:
        raise HTTPException(status_code=404, detail="Policy evaluation not found")
    return evaluation


@router.get(
    "/workspaces/{workspace_id}/policies/{policy_id}/exceptions",
    response_model=list[PolicyExceptionContract],
    summary="List scoped exceptions for a policy",
)
def list_policy_exceptions(
    workspace_id: UUID,
    policy_id: UUID,
    state: str | None = Query(default=None, alias="status", max_length=20),
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[PolicyException]:
    require_workspace_access(db, identity[0], workspace_id)
    _load_policy(db, workspace_id, policy_id)
    query = select(PolicyException).where(
        PolicyException.workspace_id == workspace_id,
        PolicyException.policy_id == policy_id,
    )
    if state:
        query = query.where(PolicyException.status == state.casefold())
    return list(db.scalars(query.order_by(PolicyException.created_at.desc())).all())


@router.post(
    "/workspaces/{workspace_id}/policies/{policy_id}/exceptions",
    response_model=PolicyExceptionContract,
    status_code=status.HTTP_201_CREATED,
    summary="Request a time-limited, narrowly scoped policy exception",
)
def request_policy_exception(
    workspace_id: UUID,
    policy_id: UUID,
    payload: PolicyExceptionCreate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> PolicyException:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=POLICY_ADMIN_ROLES)
    policy = _load_policy(db, workspace_id, policy_id)
    if not policy.enabled:
        raise HTTPException(status_code=409, detail="Disabled policies cannot receive exceptions")
    if not any(rule.get("id") == payload.rule_id for rule in policy.rules):
        raise HTTPException(status_code=422, detail="Rule does not exist in this policy version")
    now = datetime.now(UTC)
    expiry = payload.expires_at.astimezone(UTC)
    if expiry <= now or expiry > now + timedelta(days=90):
        raise HTTPException(
            status_code=422,
            detail="Exception expiry must be in the future and no more than 90 days away",
        )
    for model, resource_id, label in (
        (Service, payload.service_id, "Service"),
        (Environment, payload.environment_id, "Environment"),
    ):
        if (
            resource_id is not None
            and db.scalar(
                select(model).where(model.workspace_id == workspace_id, model.id == resource_id)
            )
            is None
        ):
            raise HTTPException(status_code=422, detail=f"{label} must belong to this workspace")
    exception = PolicyException(
        workspace_id=workspace_id,
        policy_id=policy_id,
        policy_version=policy.version,
        rule_id=payload.rule_id,
        service_id=payload.service_id,
        environment_id=payload.environment_id,
        reason=payload.reason.strip(),
        status="pending",
        requested_by=actor.id,
        expires_at=expiry,
    )
    db.add(exception)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action="policy_exception.requested",
        resource_type="policy_exception",
        resource_id=exception.id,
        workspace_id=workspace_id,
        details={
            "policy_id": str(policy_id),
            "policy_version": policy.version,
            "rule_id": payload.rule_id,
            "service_id": str(payload.service_id) if payload.service_id else None,
            "environment_id": str(payload.environment_id) if payload.environment_id else None,
            "expires_at": expiry.isoformat(),
        },
    )
    db.commit()
    db.refresh(exception)
    return exception


@router.post(
    "/workspaces/{workspace_id}/policy-exceptions/{exception_id}/decision",
    response_model=PolicyExceptionContract,
    summary="Approve or reject a policy exception",
)
def decide_policy_exception(
    workspace_id: UUID,
    exception_id: UUID,
    payload: PolicyExceptionDecision,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> PolicyException:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=POLICY_ADMIN_ROLES)
    exception = _load_exception(db, workspace_id, exception_id)
    if exception.status != "pending":
        raise HTTPException(status_code=409, detail="Only pending exceptions can be reviewed")
    if exception.requested_by == actor.id:
        raise HTTPException(
            status_code=403, detail="The requester cannot review their own exception"
        )
    now = datetime.now(UTC)
    policy = _load_policy(db, workspace_id, exception.policy_id)
    if not policy.enabled or policy.version != exception.policy_version:
        raise HTTPException(
            status_code=409,
            detail="Policy changed after this exception was requested; submit a new request",
        )
    expires_at = exception.expires_at
    expires_at = expires_at.replace(tzinfo=UTC) if expires_at.tzinfo is None else expires_at
    if expires_at <= now:
        raise HTTPException(status_code=409, detail="Policy exception request has expired")
    exception.status = "approved" if payload.decision == "approve" else "rejected"
    exception.reviewed_by = actor.id
    exception.review_comment = payload.comment
    exception.reviewed_at = now
    record_audit(
        db,
        actor=actor,
        action=f"policy_exception.{exception.status}",
        resource_type="policy_exception",
        resource_id=exception.id,
        workspace_id=workspace_id,
        details={
            "policy_id": str(exception.policy_id),
            "policy_version": exception.policy_version,
            "rule_id": exception.rule_id,
            "expires_at": exception.expires_at.isoformat(),
        },
    )
    db.commit()
    db.refresh(exception)
    return exception


@router.post(
    "/workspaces/{workspace_id}/policy-exceptions/{exception_id}/revoke",
    response_model=PolicyExceptionContract,
    summary="Revoke an approved or pending policy exception",
)
def revoke_policy_exception(
    workspace_id: UUID,
    exception_id: UUID,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> PolicyException:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=POLICY_ADMIN_ROLES)
    exception = _load_exception(db, workspace_id, exception_id)
    if exception.status not in {"approved", "pending"}:
        raise HTTPException(
            status_code=409, detail="Only pending or approved exceptions can be revoked"
        )
    exception.status = "revoked"
    exception.reviewed_by = actor.id
    exception.reviewed_at = datetime.now(UTC)
    exception.review_comment = "Revoked by an authorized workspace operator."
    record_audit(
        db,
        actor=actor,
        action="policy_exception.revoked",
        resource_type="policy_exception",
        resource_id=exception.id,
        workspace_id=workspace_id,
        details={"policy_id": str(exception.policy_id), "rule_id": exception.rule_id},
    )
    db.commit()
    db.refresh(exception)
    return exception
