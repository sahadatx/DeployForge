from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_identity, get_db, require_csrf
from app.core.audit import record_audit
from app.core.authorization import require_workspace_access
from app.models import Environment, User, UserSession, Workspace, WorkspaceMembership
from app.schemas.core import (
    EnvironmentContract,
    EnvironmentCreate,
    EnvironmentPatch,
    MembershipCreate,
    MembershipUpdate,
    WorkspaceContract,
    WorkspaceCreate,
    WorkspaceMemberContract,
)

router = APIRouter()
ENVIRONMENT_WRITER_ROLES = {"workspace_admin", "platform_engineer"}


@router.get("", response_model=list[WorkspaceContract], summary="List available workspaces")
def list_workspaces(
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[dict]:
    user = identity[0]
    if user.is_platform_admin:
        workspaces = db.scalars(select(Workspace).order_by(Workspace.name)).all()
        return [
            {**WorkspaceContract.model_validate(item).model_dump(), "role": "platform_admin"}
            for item in workspaces
        ]
    rows = db.execute(
        select(Workspace, WorkspaceMembership.role)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(WorkspaceMembership.user_id == user.id)
        .order_by(Workspace.name)
    ).all()
    return [
        {**WorkspaceContract.model_validate(workspace).model_dump(), "role": role}
        for workspace, role in rows
    ]


@router.post(
    "",
    response_model=WorkspaceContract,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace",
)
def create_workspace(
    payload: WorkspaceCreate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Workspace:
    user = identity[0]
    if not user.is_platform_admin:
        record_audit(
            db,
            actor=user,
            action="authorization.platform_workspace_create.denied",
            resource_type="workspace",
            outcome="denied",
            details={"reason": "platform_admin_required", "requested_slug": payload.slug},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Platform administrator role required"
        )
    workspace = Workspace(
        name=payload.name.strip(), slug=payload.slug, description=payload.description
    )
    db.add(workspace)
    try:
        db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role="workspace_admin",
                granted_by=user.id,
            )
        )
        record_audit(
            db,
            actor=user,
            action="workspace.created",
            resource_type="workspace",
            resource_id=workspace.id,
            workspace_id=workspace.id,
            details={"slug": workspace.slug},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Workspace slug already exists"
        ) from exc
    db.refresh(workspace)
    return workspace


@router.get(
    "/{workspace_id}/environments",
    response_model=list[EnvironmentContract],
    summary="List environments in a workspace",
)
def list_environments(
    workspace_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[Environment]:
    require_workspace_access(db, identity[0], workspace_id)
    query = (
        select(Environment)
        .where(Environment.workspace_id == workspace_id)
        .order_by(Environment.name)
    )
    return list(db.scalars(query).all())


@router.post(
    "/{workspace_id}/environments",
    response_model=EnvironmentContract,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace deployment environment",
)
def create_environment(
    workspace_id: UUID,
    payload: EnvironmentCreate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Environment:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=ENVIRONMENT_WRITER_ROLES)
    environment = Environment(
        workspace_id=workspace_id,
        slug=payload.slug,
        name=payload.name.strip(),
        environment_type=payload.environment_type,
        cluster_name=payload.cluster_name.strip() if payload.cluster_name else None,
        namespace=payload.namespace,
        protected=(
            payload.protected
            if payload.protected is not None
            else payload.environment_type == "production"
        ),
        allow_destroy=payload.allow_destroy,
        quota_cpu_millis=payload.quota_cpu_millis,
        quota_memory_mib=payload.quota_memory_mib,
        quota_pods=payload.quota_pods,
        quota_persistent_volume_claims=payload.quota_persistent_volume_claims,
        quota_services=payload.quota_services,
    )
    if (
        environment.environment_type == "production"
        and environment.protected is False
        and not actor.is_platform_admin
    ):
        raise HTTPException(
            status_code=403,
            detail="Only a platform administrator can remove production protection",
        )
    if (
        environment.environment_type == "production"
        and environment.allow_destroy
        and not actor.is_platform_admin
    ):
        raise HTTPException(
            status_code=403,
            detail="Only a platform administrator can enable destructive production plans",
        )
    db.add(environment)
    try:
        db.flush()
        record_audit(
            db,
            actor=actor,
            action="environment.created",
            resource_type="environment",
            resource_id=environment.id,
            workspace_id=workspace_id,
            details={"slug": environment.slug, "environment_type": environment.environment_type},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Environment slug already exists in this workspace"
        ) from exc
    db.refresh(environment)
    return environment


@router.patch(
    "/{workspace_id}/environments/{environment_id}",
    response_model=EnvironmentContract,
    summary="Update environment protection, cluster binding, namespace, and quotas",
)
def patch_environment(
    workspace_id: UUID,
    environment_id: UUID,
    payload: EnvironmentPatch,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Environment:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=ENVIRONMENT_WRITER_ROLES)
    environment = db.scalar(
        select(Environment).where(
            Environment.workspace_id == workspace_id,
            Environment.id == environment_id,
        )
    )
    if environment is None:
        raise HTTPException(status_code=404, detail="Environment not found")
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Provide at least one environment setting")
    if any(
        updates.get(field) is None
        for field in ("name", "protected", "allow_destroy")
        if field in updates
    ):
        raise HTTPException(
            status_code=422, detail="Environment name and protection flags cannot be null"
        )
    if (
        environment.environment_type == "production"
        and updates.get("allow_destroy") is True
        and not actor.is_platform_admin
    ):
        raise HTTPException(
            status_code=403,
            detail="Only a platform administrator can enable destructive production plans",
        )
    if (
        environment.environment_type == "production"
        and updates.get("protected") is False
        and not actor.is_platform_admin
    ):
        raise HTTPException(
            status_code=403,
            detail="Only a platform administrator can remove production protection",
        )
    for field in ("cluster_name", "namespace"):
        if field in updates and isinstance(updates[field], str):
            updates[field] = updates[field].strip() or None
    for field, value in updates.items():
        setattr(environment, field, value)
    record_audit(
        db,
        actor=actor,
        action="environment.updated",
        resource_type="environment",
        resource_id=environment.id,
        workspace_id=workspace_id,
        details={"updated_fields": sorted(updates)},
    )
    db.commit()
    db.refresh(environment)
    return environment


@router.get(
    "/{workspace_id}/members",
    response_model=list[WorkspaceMemberContract],
    summary="List workspace members and roles",
)
def list_members(
    workspace_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[dict]:
    require_workspace_access(db, identity[0], workspace_id)
    memberships = db.scalars(
        select(WorkspaceMembership)
        .where(WorkspaceMembership.workspace_id == workspace_id)
        .order_by(WorkspaceMembership.created_at)
    ).all()
    return [
        {
            "membership_id": membership.id,
            "user": membership.user,
            "role": membership.role,
            "created_at": membership.created_at,
        }
        for membership in memberships
    ]


@router.post(
    "/{workspace_id}/members",
    response_model=WorkspaceMemberContract,
    status_code=status.HTTP_201_CREATED,
    summary="Grant workspace access to an existing user",
)
def create_member(
    workspace_id: UUID,
    payload: MembershipCreate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles={"workspace_admin"})
    if payload.role == "workspace_admin" and not actor.is_platform_admin:
        record_audit(
            db,
            actor=actor,
            action="authorization.workspace_admin_grant.denied",
            resource_type="workspace",
            resource_id=workspace_id,
            workspace_id=workspace_id,
            outcome="denied",
            details={"reason": "platform_admin_required"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a platform administrator may grant workspace admin",
        )
    email = str(payload.email).casefold()
    user = db.scalar(select(User).where(func.lower(User.email) == email, User.active.is_(True)))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User must sign in before workspace access can be granted",
        )
    existing = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user.id,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="User already belongs to this workspace"
        )
    membership = WorkspaceMembership(
        workspace_id=workspace_id,
        user_id=user.id,
        role=payload.role,
        granted_by=actor.id,
    )
    db.add(membership)
    record_audit(
        db,
        actor=actor,
        action="workspace.membership.granted",
        resource_type="workspace_membership",
        resource_id=membership.id,
        workspace_id=workspace_id,
        details={"target_user_id": str(user.id), "role": payload.role},
    )
    db.commit()
    db.refresh(membership)
    return {
        "membership_id": membership.id,
        "user": user,
        "role": membership.role,
        "created_at": membership.created_at,
    }


@router.patch(
    "/{workspace_id}/members/{user_id}",
    response_model=WorkspaceMemberContract,
    summary="Update a workspace member role",
)
def update_member(
    workspace_id: UUID,
    user_id: UUID,
    payload: MembershipUpdate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles={"workspace_admin"})
    if payload.role == "workspace_admin" and not actor.is_platform_admin:
        record_audit(
            db,
            actor=actor,
            action="authorization.workspace_admin_grant.denied",
            resource_type="workspace",
            resource_id=workspace_id,
            workspace_id=workspace_id,
            outcome="denied",
            details={"reason": "platform_admin_required"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a platform administrator may grant workspace admin",
        )
    membership = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace member not found"
        )
    if membership.role == "workspace_admin" and payload.role != "workspace_admin":
        admin_count = (
            db.scalar(
                select(func.count())
                .select_from(WorkspaceMembership)
                .where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.role == "workspace_admin",
                )
            )
            or 0
        )
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The last workspace administrator cannot be demoted",
            )
    previous_role = membership.role
    membership.role = payload.role
    record_audit(
        db,
        actor=actor,
        action="workspace.membership.role_changed",
        resource_type="workspace_membership",
        resource_id=membership.id,
        workspace_id=workspace_id,
        details={
            "target_user_id": str(user_id),
            "from_role": previous_role,
            "to_role": payload.role,
        },
    )
    db.commit()
    db.refresh(membership)
    return {
        "membership_id": membership.id,
        "user": membership.user,
        "role": membership.role,
        "created_at": membership.created_at,
    }


@router.delete(
    "/{workspace_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke workspace access",
)
def delete_member(
    workspace_id: UUID,
    user_id: UUID,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles={"workspace_admin"})
    membership = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace member not found"
        )
    if membership.role == "workspace_admin":
        admin_count = (
            db.scalar(
                select(func.count())
                .select_from(WorkspaceMembership)
                .where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.role == "workspace_admin",
                )
            )
            or 0
        )
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The last workspace administrator cannot be removed",
            )
    record_audit(
        db,
        actor=actor,
        action="workspace.membership.revoked",
        resource_type="workspace_membership",
        resource_id=membership.id,
        workspace_id=workspace_id,
        details={"target_user_id": str(user_id), "role": membership.role},
    )
    db.delete(membership)
    db.commit()
