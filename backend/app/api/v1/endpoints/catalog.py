from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_identity, get_db, require_csrf
from app.core.audit import record_audit
from app.core.authorization import require_workspace_access
from app.models import GoldenPath, Service, ServiceTeam, User, UserSession
from app.schemas.core import (
    GoldenPathContract,
    GoldenPathInstantiate,
    ServiceCatalogResponse,
    ServiceCatalogSummary,
    ServiceContract,
    ServiceCreate,
    ServiceLifecycle,
    ServiceTeamContract,
    ServiceTeamCreate,
    ServiceUpdate,
)

router = APIRouter()
CATALOG_WRITER_ROLES = {"workspace_admin", "platform_engineer"}


def _service_contract(service: Service) -> ServiceContract:
    return ServiceContract(
        id=service.id,
        workspace_id=service.workspace_id,
        slug=service.slug,
        name=service.name,
        description=service.description,
        system=service.system,
        owner_team_id=service.owner_team_id,
        owner_team_name=service.team.name if service.team else None,
        repository_url=service.repository_url,
        documentation_url=service.documentation_url,
        lifecycle=service.lifecycle,
        criticality=service.criticality,
        tags=service.tags or [],
        metadata=service.metadata_ or {},
        golden_path_id=service.golden_path_id,
        golden_path_slug=(service.golden_path.slug if service.golden_path else None),
        created_by=service.created_by,
        created_at=service.created_at,
        updated_at=service.updated_at,
    )


def _load_service(db: Session, workspace_id: UUID, service_id: UUID) -> Service:
    service = db.scalar(
        select(Service)
        .options(joinedload(Service.team), joinedload(Service.golden_path))
        .where(Service.workspace_id == workspace_id, Service.id == service_id)
    )
    if service is None:
        raise HTTPException(status_code=404, detail="Service not found")
    return service


def _validate_team(db: Session, workspace_id: UUID, team_id: UUID) -> ServiceTeam:
    team = db.scalar(
        select(ServiceTeam).where(
            ServiceTeam.workspace_id == workspace_id,
            ServiceTeam.id == team_id,
        )
    )
    if team is None:
        raise HTTPException(status_code=422, detail="Owner team must belong to this workspace")
    return team


def _create_service(
    db: Session,
    *,
    workspace_id: UUID,
    actor: User,
    payload: ServiceCreate,
    golden_path: GoldenPath | None = None,
) -> Service:
    team = _validate_team(db, workspace_id, payload.owner_team_id)
    metadata = dict(payload.metadata)
    if golden_path:
        schema = {field["key"]: field for field in golden_path.config_schema}
        unknown_keys = set(metadata) - set(schema)
        if unknown_keys:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported golden path configuration: {', '.join(sorted(unknown_keys))}",
            )
        metadata = {**golden_path.defaults, **metadata}
        for key, field in schema.items():
            value = metadata.get(key)
            if field.get("required", field.get("type") == "select") and value in (None, ""):
                raise HTTPException(
                    status_code=422, detail=f"Golden path field '{key}' is required"
                )
            if field.get("type") == "select" and value is not None:
                if value not in field.get("options", []):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Unsupported value for golden path field '{key}'",
                    )
            if field.get("type") == "text" and value is not None and not isinstance(value, str):
                raise HTTPException(
                    status_code=422, detail=f"Golden path field '{key}' must be text"
                )
    values: dict[str, Any] = payload.model_dump(exclude={"metadata", "owner_team_id"})
    service = Service(
        workspace_id=workspace_id,
        owner_team_id=team.id,
        created_by=actor.id,
        metadata_=metadata,
        **values,
    )
    if golden_path is not None:
        service.golden_path_id = golden_path.id
        service.golden_path_snapshot = {
            "slug": golden_path.slug,
            "name": golden_path.name,
            "version": golden_path.version,
        }
    db.add(service)
    return service


@router.get(
    "/workspaces/{workspace_id}/teams",
    response_model=list[ServiceTeamContract],
    summary="List service ownership teams",
)
def list_teams(
    workspace_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    require_workspace_access(db, identity[0], workspace_id)
    rows = db.execute(
        select(ServiceTeam, func.count(Service.id))
        .outerjoin(Service, Service.owner_team_id == ServiceTeam.id)
        .where(ServiceTeam.workspace_id == workspace_id)
        .group_by(ServiceTeam.id)
        .order_by(ServiceTeam.name)
    ).all()
    return [
        {
            **ServiceTeamContract.model_validate(team).model_dump(),
            "service_count": count,
        }
        for team, count in rows
    ]


@router.post(
    "/workspaces/{workspace_id}/teams",
    response_model=ServiceTeamContract,
    status_code=status.HTTP_201_CREATED,
    summary="Create a service ownership team",
)
def create_team(
    workspace_id: UUID,
    payload: ServiceTeamCreate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ServiceTeam:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=CATALOG_WRITER_ROLES)
    team = ServiceTeam(
        workspace_id=workspace_id,
        slug=payload.slug,
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
    )
    db.add(team)
    try:
        db.flush()
        record_audit(
            db,
            actor=actor,
            action="catalog.team.created",
            resource_type="service_team",
            resource_id=team.id,
            workspace_id=workspace_id,
            details={"slug": team.slug, "name": team.name},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Team slug already exists in this workspace"
        ) from exc
    db.refresh(team)
    return team


@router.get(
    "/workspaces/{workspace_id}/golden-paths",
    response_model=list[GoldenPathContract],
    summary="List available golden paths",
)
def list_golden_paths(
    workspace_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> list[GoldenPath]:
    require_workspace_access(db, identity[0], workspace_id)
    return list(
        db.scalars(
            select(GoldenPath).where(GoldenPath.status == "available").order_by(GoldenPath.name)
        ).all()
    )


@router.get(
    "/workspaces/{workspace_id}/services/summary",
    response_model=ServiceCatalogSummary,
    summary="Summarize service catalog health",
)
def service_summary(
    workspace_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> ServiceCatalogSummary:
    require_workspace_access(db, identity[0], workspace_id)
    lifecycle_counts = dict(
        db.execute(
            select(Service.lifecycle, func.count(Service.id))
            .where(Service.workspace_id == workspace_id)
            .group_by(Service.lifecycle)
        ).all()
    )
    return ServiceCatalogSummary(
        total=sum(lifecycle_counts.values()),
        production=lifecycle_counts.get("production", 0),
        experimental=lifecycle_counts.get("experimental", 0),
        ownership_teams=db.scalar(
            select(func.count(ServiceTeam.id)).where(ServiceTeam.workspace_id == workspace_id)
        )
        or 0,
    )


@router.get(
    "/workspaces/{workspace_id}/services",
    response_model=ServiceCatalogResponse,
    summary="Search the workspace service catalog",
)
def list_services(
    workspace_id: UUID,
    search: str | None = Query(default=None, max_length=120),
    lifecycle: ServiceLifecycle | None = None,
    owner_team_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> ServiceCatalogResponse:
    require_workspace_access(db, identity[0], workspace_id)
    query = select(Service).where(Service.workspace_id == workspace_id)
    if lifecycle:
        query = query.where(Service.lifecycle == lifecycle)
    if owner_team_id:
        query = query.where(Service.owner_team_id == owner_team_id)
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.outerjoin(ServiceTeam, Service.owner_team_id == ServiceTeam.id).where(
            or_(
                Service.name.ilike(term),
                Service.slug.ilike(term),
                Service.description.ilike(term),
                Service.system.ilike(term),
                Service.repository_url.ilike(term),
                ServiceTeam.name.ilike(term),
                cast(Service.tags, String).ilike(term),
            )
        )
    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    total = db.scalar(count_query) or 0
    services = (
        db.scalars(
            query.options(joinedload(Service.team), joinedload(Service.golden_path))
            .order_by(Service.name)
            .limit(limit)
            .offset(offset)
        )
        .unique()
        .all()
    )
    return ServiceCatalogResponse(
        items=[_service_contract(service) for service in services],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/workspaces/{workspace_id}/services",
    response_model=ServiceContract,
    status_code=status.HTTP_201_CREATED,
    summary="Register a service in the catalog",
)
def create_service(
    workspace_id: UUID,
    payload: ServiceCreate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ServiceContract:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=CATALOG_WRITER_ROLES)
    service = _create_service(db, workspace_id=workspace_id, actor=actor, payload=payload)
    try:
        db.flush()
        record_audit(
            db,
            actor=actor,
            action="catalog.service.created",
            resource_type="service",
            resource_id=service.id,
            workspace_id=workspace_id,
            details={"slug": service.slug, "lifecycle": service.lifecycle},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Service slug already exists in this workspace"
        ) from exc
    return _service_contract(_load_service(db, workspace_id, service.id))


@router.post(
    "/workspaces/{workspace_id}/golden-paths/{golden_path_slug}/instantiate",
    response_model=ServiceContract,
    status_code=status.HTTP_201_CREATED,
    summary="Create a catalog service from a golden path",
)
def instantiate_golden_path(
    workspace_id: UUID,
    golden_path_slug: str,
    payload: GoldenPathInstantiate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ServiceContract:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=CATALOG_WRITER_ROLES)
    golden_path = db.scalar(
        select(GoldenPath).where(
            GoldenPath.slug == golden_path_slug,
            GoldenPath.status == "available",
        )
    )
    if golden_path is None:
        raise HTTPException(status_code=404, detail="Golden path not found")
    service = _create_service(
        db,
        workspace_id=workspace_id,
        actor=actor,
        payload=payload,
        golden_path=golden_path,
    )
    try:
        db.flush()
        record_audit(
            db,
            actor=actor,
            action="catalog.golden_path.instantiated",
            resource_type="service",
            resource_id=service.id,
            workspace_id=workspace_id,
            details={"golden_path": golden_path.slug, "service_slug": service.slug},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Service slug already exists in this workspace"
        ) from exc
    return _service_contract(_load_service(db, workspace_id, service.id))


@router.get(
    "/workspaces/{workspace_id}/services/{service_id}",
    response_model=ServiceContract,
    summary="Get a catalog service",
)
def get_service(
    workspace_id: UUID,
    service_id: UUID,
    identity: tuple[User, UserSession] = Depends(get_current_identity),
    db: Session = Depends(get_db),
) -> ServiceContract:
    require_workspace_access(db, identity[0], workspace_id)
    return _service_contract(_load_service(db, workspace_id, service_id))


@router.patch(
    "/workspaces/{workspace_id}/services/{service_id}",
    response_model=ServiceContract,
    summary="Update catalog metadata for a service",
)
def update_service(
    workspace_id: UUID,
    service_id: UUID,
    payload: ServiceUpdate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ServiceContract:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=CATALOG_WRITER_ROLES)
    service = _load_service(db, workspace_id, service_id)
    changes = payload.model_dump(exclude_unset=True, by_alias=True)
    if not changes:
        raise HTTPException(status_code=422, detail="At least one field must be provided")
    if "owner_team_id" in changes:
        team_id = changes.pop("owner_team_id")
        if team_id is None:
            raise HTTPException(status_code=422, detail="A service must have an owner team")
        _validate_team(db, workspace_id, team_id)
        service.owner_team_id = team_id
    if "metadata" in changes:
        service.metadata_ = changes.pop("metadata") or {}
    for key, value in changes.items():
        setattr(service, key, value)
    record_audit(
        db,
        actor=actor,
        action=(
            "catalog.service.archived"
            if service.lifecycle == "retired"
            else "catalog.service.updated"
        ),
        resource_type="service",
        resource_id=service.id,
        workspace_id=workspace_id,
        details={"changed_fields": sorted(payload.model_fields_set)},
    )
    db.commit()
    return _service_contract(_load_service(db, workspace_id, service.id))


@router.post(
    "/workspaces/{workspace_id}/services/{service_id}/archive",
    response_model=ServiceContract,
    summary="Retire a service without deleting its history",
)
def archive_service(
    workspace_id: UUID,
    service_id: UUID,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ServiceContract:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=CATALOG_WRITER_ROLES)
    service = _load_service(db, workspace_id, service_id)
    if service.lifecycle != "retired":
        previous_lifecycle = service.lifecycle
        service.lifecycle = "retired"
        record_audit(
            db,
            actor=actor,
            action="catalog.service.archived",
            resource_type="service",
            resource_id=service.id,
            workspace_id=workspace_id,
            details={"previous_lifecycle": previous_lifecycle},
        )
        db.commit()
    return _service_contract(_load_service(db, workspace_id, service.id))
