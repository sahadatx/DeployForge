"""Token-protected SCIM 2.0 user and group lifecycle endpoints."""

import hmac
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.audit import record_audit
from app.core.config import settings
from app.core.identity_sync import scim_group_names, sync_group_memberships
from app.models import (
    SCIMGroup,
    SCIMGroupMembership,
    User,
    UserSession,
    WorkspaceMembership,
)

router = APIRouter()
SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCIM_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
SCIM_ISSUER = "urn:deployforge:scim"
EMAIL_ADAPTER = TypeAdapter(EmailStr)


class SCIMName(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    formatted: str | None = None
    given_name: str | None = Field(default=None, alias="givenName")
    family_name: str | None = Field(default=None, alias="familyName")


class SCIMEmail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: str = Field(min_length=3, max_length=320)
    primary: bool = False


class SCIMUserCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    schemas: list[str] = Field(default_factory=lambda: [SCIM_USER_SCHEMA])
    external_id: str = Field(alias="externalId", min_length=1, max_length=255)
    user_name: str = Field(alias="userName", min_length=1, max_length=320)
    display_name: str | None = Field(default=None, alias="displayName", max_length=180)
    name: SCIMName | None = None
    emails: list[SCIMEmail] = Field(default_factory=list)
    active: bool = True

    @field_validator("external_id", "user_name", mode="before")
    @classmethod
    def normalize_identity_key(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class SCIMUserPatchOperation(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    op: str
    path: str | None = None
    value: Any = None


class SCIMUserPatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    schemas: list[str] = Field(default_factory=list)
    operations: list[SCIMUserPatchOperation] = Field(alias="Operations", min_length=1)


class SCIMGroupMemberInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: str = Field(min_length=1, max_length=64)
    display: str | None = None


class SCIMGroupCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    schemas: list[str] = Field(default_factory=lambda: [SCIM_GROUP_SCHEMA])
    external_id: str | None = Field(default=None, alias="externalId", max_length=255)
    display_name: str = Field(alias="displayName", min_length=1, max_length=180)
    members: list[SCIMGroupMemberInput] = Field(default_factory=list)

    @field_validator("external_id", mode="before")
    @classmethod
    def normalize_external_id(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class SCIMGroupPatchOperation(BaseModel):
    model_config = ConfigDict(extra="allow")

    op: str
    path: str | None = None
    value: Any = None


class SCIMGroupPatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    schemas: list[str] = Field(default_factory=list)
    operations: list[SCIMGroupPatchOperation] = Field(alias="Operations", min_length=1)


def _error(status_code: int, detail: str, *, scim_type: str | None = None) -> JSONResponse:
    body: dict[str, object] = {
        "schemas": [SCIM_ERROR_SCHEMA],
        "status": str(status_code),
        "detail": detail,
    }
    if scim_type:
        body["scimType"] = scim_type
    return JSONResponse(status_code=status_code, content=body)


def _authorize(request: Request) -> JSONResponse | None:
    configured = settings.scim_bearer_token
    if configured is None:
        return _error(503, "SCIM provisioning is not configured")
    supplied = request.headers.get("authorization", "")
    scheme, _, credential = supplied.partition(" ")
    if scheme.casefold() != "bearer" or not credential:
        return _error(401, "A bearer token is required")
    if not hmac.compare_digest(credential, configured.get_secret_value()):
        return _error(401, "The bearer token is invalid")
    return None


async def _body(request: Request, model: type[BaseModel]):
    try:
        data = await request.json()
        return model.model_validate(data), None
    except (ValidationError, ValueError, TypeError):
        return None, _error(400, "The SCIM request body is invalid", scim_type="invalidSyntax")


def _meta(
    resource_type: str,
    resource_id: str,
    created: datetime,
    modified: datetime,
    request: Request,
) -> dict:
    return {
        "resourceType": resource_type,
        "created": created.astimezone(UTC).isoformat(),
        "lastModified": modified.astimezone(UTC).isoformat(),
        "location": f"{str(request.base_url).rstrip('/')}/scim/v2/{resource_type}s/{resource_id}",
    }


def _group_contract(group: SCIMGroup, request: Request, db: Session) -> dict[str, object]:
    memberships = db.scalars(
        select(SCIMGroupMembership)
        .where(SCIMGroupMembership.group_id == group.id)
        .order_by(SCIMGroupMembership.user_id)
    ).all()
    return {
        "schemas": [SCIM_GROUP_SCHEMA],
        "id": str(group.id),
        "externalId": group.external_id,
        "displayName": group.display_name,
        "members": [
            {
                "value": str(item.user_id),
                "$ref": f"{str(request.base_url).rstrip('/')}/scim/v2/Users/{item.user_id}",
                "type": "User",
            }
            for item in memberships
        ],
        "meta": _meta("Group", str(group.id), group.created_at, group.updated_at, request),
    }


def _user_contract(user: User, request: Request, db: Session) -> dict[str, object]:
    display_name = user.display_name
    memberships = db.execute(
        select(SCIMGroupMembership, SCIMGroup)
        .join(SCIMGroup, SCIMGroup.id == SCIMGroupMembership.group_id)
        .where(SCIMGroupMembership.user_id == user.id)
        .order_by(SCIMGroup.display_name)
    ).all()
    return {
        "schemas": [SCIM_USER_SCHEMA],
        "id": str(user.id),
        "externalId": user.scim_external_id,
        "userName": user.email,
        "displayName": display_name,
        "name": {"formatted": display_name},
        "emails": [{"value": user.email, "primary": True, "type": "work"}],
        "active": user.active,
        "groups": [
            {
                "value": str(group.id),
                "$ref": f"{str(request.base_url).rstrip('/')}/scim/v2/Groups/{group.id}",
                "display": group.display_name,
                "type": "direct",
            }
            for _membership, group in memberships
        ],
        "meta": _meta("User", str(user.id), user.created_at, user.updated_at, request),
    }


def _sync_user_groups(db: Session, user: User) -> None:
    if user.active:
        sync_group_memberships(db, user, scim_group_names(db, user))


def _valid_email(payload: SCIMUserCreate) -> str:
    primary = next((email.value for email in payload.emails if email.primary), None)
    value = primary or (payload.emails[0].value if payload.emails else payload.user_name)
    return str(EMAIL_ADAPTER.validate_python(value)).casefold()


def _filter_expression(value: str | None, attribute: str) -> str | None:
    if not value:
        return None
    match = re.fullmatch(rf'{attribute}\s+eq\s+"([^"\\]*)"', value, flags=re.IGNORECASE)
    return match.group(1) if match else None


@router.get("/ServiceProviderConfig", include_in_schema=False)
def service_provider_config(request: Request):
    denied = _authorize(request)
    if denied:
        return denied
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 100},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "Bearer",
                "description": "SCIM bearer token configured by the DeployForge operator",
                "primary": True,
            }
        ],
    }


@router.get("/Users", include_in_schema=False)
def list_users(
    request: Request,
    filter_value: str | None = Query(default=None, alias="filter", max_length=255),
    start_index: int = Query(default=1, alias="startIndex", ge=1),
    count: int = Query(default=100, ge=0, le=100),
    db: Session = Depends(get_db),
):
    denied = _authorize(request)
    if denied:
        return denied
    query = select(User).where(User.scim_external_id.is_not(None))
    user_name = _filter_expression(filter_value, "userName")
    if filter_value and user_name is None:
        return _error(
            400,
            "Only an exact userName eq filter is supported",
            scim_type="invalidFilter",
        )
    if user_name:
        query = query.where(func.lower(User.email) == user_name.casefold())
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    resources = db.scalars(query.order_by(User.email).offset(start_index - 1).limit(count)).all()
    return {
        "schemas": [SCIM_LIST_SCHEMA],
        "totalResults": total,
        "startIndex": start_index,
        "itemsPerPage": len(resources),
        "Resources": [_user_contract(user, request, db) for user in resources],
    }


@router.post("/Users", status_code=201, include_in_schema=False)
async def create_user(request: Request, db: Session = Depends(get_db)):
    denied = _authorize(request)
    if denied:
        return denied
    payload, error = await _body(request, SCIMUserCreate)
    if error:
        return error
    try:
        email = _valid_email(payload)
    except ValidationError:
        return _error(400, "A valid work email is required", scim_type="invalidValue")

    user = db.scalar(select(User).where(User.scim_external_id == payload.external_id))
    if user is not None:
        return _error(409, "SCIM externalId is already provisioned")
    email_owner = db.scalar(select(User).where(func.lower(User.email) == email))
    if email_owner is not None:
        return _error(
            409,
            "Email already belongs to another identity; provision SCIM before first OIDC sign-in",
        )
    name = payload.display_name or (payload.name.formatted if payload.name else None)
    user = User(
        issuer=SCIM_ISSUER,
        subject=payload.external_id,
        scim_external_id=payload.external_id,
        email=email,
        display_name=(name or email.split("@", 1)[0])[:180],
        email_verified=False,
        active=payload.active,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return _error(409, "SCIM externalId or email already exists")
    record_audit(
        db,
        action="identity.scim.user.provisioned",
        resource_type="user",
        resource_id=user.id,
        details={"external_id": payload.external_id, "active": payload.active},
    )
    db.commit()
    db.refresh(user)
    return JSONResponse(
        status_code=201,
        content=_user_contract(user, request, db),
        headers={
            "Location": _meta("User", str(user.id), user.created_at, user.updated_at, request)[
                "location"
            ]
        },
    )


@router.get("/Users/{user_id}", include_in_schema=False)
def get_user(user_id: UUID, request: Request, db: Session = Depends(get_db)):
    denied = _authorize(request)
    if denied:
        return denied
    user = db.scalar(select(User).where(User.id == user_id, User.scim_external_id.is_not(None)))
    return _user_contract(user, request, db) if user else _error(404, "SCIM user not found")


def _deprovision_user(db: Session, user: User) -> None:
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    memberships = db.scalars(
        select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)
    ).all()
    for membership in memberships:
        record_audit(
            db,
            action="identity.scim.membership.revoked",
            resource_type="workspace_membership",
            resource_id=membership.id,
            workspace_id=membership.workspace_id,
            details={"target_user_id": str(user.id), "previous_role": membership.role},
        )
        db.delete(membership)
    record_audit(
        db,
        action="identity.scim.user.deprovisioned",
        resource_type="user",
        resource_id=user.id,
        details={"sessions_revoked": True},
    )


@router.patch("/Users/{user_id}", include_in_schema=False)
async def patch_user(user_id: UUID, request: Request, db: Session = Depends(get_db)):
    denied = _authorize(request)
    if denied:
        return denied
    user = db.scalar(select(User).where(User.id == user_id, User.scim_external_id.is_not(None)))
    if user is None:
        return _error(404, "SCIM user not found")
    payload, error = await _body(request, SCIMUserPatch)
    if error:
        return error
    changed = False
    was_active = user.active
    for operation in payload.operations:
        op_name = operation.op.casefold()
        if op_name not in {"replace", "add"}:
            return _error(400, "Only add and replace User patch operations are supported")
        path = (operation.path or "").casefold()
        value = operation.value
        if path in {"active", ""} and isinstance(value, dict) and "active" in value:
            value = value["active"]
            path = "active"
        if path == "active" and isinstance(value, bool):
            if user.active and not value:
                _deprovision_user(db, user)
            user.active = value
            changed = True
        elif path in {"displayname", "name.formatted"} and isinstance(value, str):
            display_name = value.strip()
            if not display_name:
                return _error(400, "SCIM displayName cannot be blank", scim_type="invalidValue")
            user.display_name = display_name[:180]
            changed = True
        elif path in {"emails", "emails[type eq \"work\"].value"}:
            candidate = value[0].get("value") if isinstance(value, list) and value else value
            if isinstance(candidate, str):
                try:
                    normalized = str(EMAIL_ADAPTER.validate_python(candidate)).casefold()
                except ValidationError:
                    return _error(400, "A valid work email is required", scim_type="invalidValue")
                owner = db.scalar(
                    select(User).where(func.lower(User.email) == normalized, User.id != user.id)
                )
                if owner:
                    return _error(409, "Email already belongs to another identity")
                if user.email != normalized:
                    user.email = normalized
                    user.email_verified = False
                changed = True
        else:
            return _error(400, f"Unsupported SCIM User patch path: {operation.path or '(missing)'}")
    if not was_active and user.active:
        _sync_user_groups(db, user)
    if changed:
        record_audit(
            db,
            action="identity.scim.user.updated",
            resource_type="user",
            resource_id=user.id,
            details={"active": user.active},
        )
    db.commit()
    db.refresh(user)
    return _user_contract(user, request, db)


@router.put("/Users/{user_id}", include_in_schema=False)
async def replace_user(user_id: UUID, request: Request, db: Session = Depends(get_db)):
    denied = _authorize(request)
    if denied:
        return denied
    user = db.scalar(select(User).where(User.id == user_id, User.scim_external_id.is_not(None)))
    if user is None:
        return _error(404, "SCIM user not found")
    payload, error = await _body(request, SCIMUserCreate)
    if error:
        return error
    if payload.external_id != user.scim_external_id:
        return _error(400, "SCIM externalId is immutable", scim_type="mutability")
    try:
        email = _valid_email(payload)
    except ValidationError:
        return _error(400, "A valid work email is required", scim_type="invalidValue")
    email_owner = db.scalar(
        select(User).where(func.lower(User.email) == email, User.id != user.id)
    )
    if email_owner:
        return _error(409, "Email already belongs to another identity")

    was_active = user.active
    if was_active and not payload.active:
        _deprovision_user(db, user)
    if email != user.email:
        user.email = email
        user.email_verified = False
    user.display_name = (
        payload.display_name
        or (payload.name.formatted if payload.name else None)
        or email.split("@", 1)[0]
    )[:180]
    user.active = payload.active
    if not was_active and user.active:
        _sync_user_groups(db, user)
    record_audit(
        db,
        action="identity.scim.user.replaced",
        resource_type="user",
        resource_id=user.id,
        details={"active": user.active},
    )
    db.commit()
    db.refresh(user)
    return _user_contract(user, request, db)


@router.delete("/Users/{user_id}", status_code=204, include_in_schema=False)
def delete_user(user_id: UUID, request: Request, db: Session = Depends(get_db)):
    denied = _authorize(request)
    if denied:
        return denied
    user = db.scalar(select(User).where(User.id == user_id, User.scim_external_id.is_not(None)))
    if user is None:
        return _error(404, "SCIM user not found")
    if user.active:
        _deprovision_user(db, user)
        user.active = False
        db.commit()
    return Response(status_code=204)


@router.get("/Groups", include_in_schema=False)
def list_groups(
    request: Request,
    filter_value: str | None = Query(default=None, alias="filter", max_length=255),
    start_index: int = Query(default=1, alias="startIndex", ge=1),
    count: int = Query(default=100, ge=0, le=100),
    db: Session = Depends(get_db),
):
    denied = _authorize(request)
    if denied:
        return denied
    query = select(SCIMGroup)
    display_name = _filter_expression(filter_value, "displayName")
    if filter_value and display_name is None:
        return _error(
            400,
            "Only an exact displayName eq filter is supported",
            scim_type="invalidFilter",
        )
    if display_name:
        query = query.where(func.lower(SCIMGroup.display_name) == display_name.casefold())
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    resources = db.scalars(
        query.order_by(SCIMGroup.display_name).offset(start_index - 1).limit(count)
    ).all()
    return {
        "schemas": [SCIM_LIST_SCHEMA],
        "totalResults": total,
        "startIndex": start_index,
        "itemsPerPage": len(resources),
        "Resources": [_group_contract(group, request, db) for group in resources],
    }


def _apply_group_members(db: Session, group: SCIMGroup, members: list[SCIMGroupMemberInput]):
    desired_ids: set[UUID] = set()
    for member in members:
        try:
            user_id = UUID(member.value)
        except ValueError:
            return "Group member value must be a DeployForge SCIM User ID"
        user = db.scalar(
            select(User).where(User.id == user_id, User.scim_external_id.is_not(None))
        )
        if user is None:
            return "Group member does not exist"
        desired_ids.add(user_id)
    current = db.scalars(
        select(SCIMGroupMembership).where(SCIMGroupMembership.group_id == group.id)
    ).all()
    current_ids = {item.user_id for item in current}
    for user_id in current_ids - desired_ids:
        db.execute(
            delete(SCIMGroupMembership).where(
                SCIMGroupMembership.group_id == group.id,
                SCIMGroupMembership.user_id == user_id,
            )
        )
    for user_id in desired_ids - current_ids:
        db.add(SCIMGroupMembership(group_id=group.id, user_id=user_id))
    return None


@router.post("/Groups", status_code=201, include_in_schema=False)
async def create_group(request: Request, db: Session = Depends(get_db)):
    denied = _authorize(request)
    if denied:
        return denied
    payload, error = await _body(request, SCIMGroupCreate)
    if error:
        return error
    group = SCIMGroup(
        external_id=payload.external_id,
        display_name=payload.display_name.strip(),
    )
    db.add(group)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return _error(409, "SCIM group displayName or externalId already exists")
    member_error = _apply_group_members(db, group, payload.members)
    if member_error:
        db.rollback()
        return _error(400, member_error, scim_type="invalidValue")
    db.flush()
    members = db.scalars(
        select(SCIMGroupMembership.user_id).where(SCIMGroupMembership.group_id == group.id)
    ).all()
    for user_id in members:
        user = db.get(User, user_id)
        if user:
            _sync_user_groups(db, user)
    record_audit(
        db,
        action="identity.scim.group.provisioned",
        resource_type="identity_group",
        resource_id=group.id,
        details={"display_name": group.display_name, "member_count": len(members)},
    )
    db.commit()
    db.refresh(group)
    location = _meta(
        "Group", str(group.id), group.created_at, group.updated_at, request
    )["location"]
    return JSONResponse(
        status_code=201,
        content=_group_contract(group, request, db),
        headers={"Location": location},
    )


@router.get("/Groups/{group_id}", include_in_schema=False)
def get_group(group_id: UUID, request: Request, db: Session = Depends(get_db)):
    denied = _authorize(request)
    if denied:
        return denied
    group = db.get(SCIMGroup, group_id)
    return _group_contract(group, request, db) if group else _error(404, "SCIM group not found")


@router.patch("/Groups/{group_id}", include_in_schema=False)
async def patch_group(group_id: UUID, request: Request, db: Session = Depends(get_db)):
    denied = _authorize(request)
    if denied:
        return denied
    group = db.get(SCIMGroup, group_id)
    if group is None:
        return _error(404, "SCIM group not found")
    payload, error = await _body(request, SCIMGroupPatch)
    if error:
        return error
    affected_users: set[UUID] = set()
    for operation in payload.operations:
        op_name = operation.op.casefold()
        path = (operation.path or "").casefold()
        value = operation.value
        if path == "displayname" and op_name in {"replace", "add"} and isinstance(value, str):
            display_name = value.strip()
            if not display_name:
                return _error(400, "SCIM displayName cannot be blank", scim_type="invalidValue")
            group.display_name = display_name[:180]
            affected_users.update(
                db.scalars(
                    select(SCIMGroupMembership.user_id).where(
                        SCIMGroupMembership.group_id == group.id
                    )
                ).all()
            )
        elif path == "members" and op_name in {"add", "replace"}:
            try:
                members = [SCIMGroupMemberInput.model_validate(item) for item in value]
            except (ValidationError, TypeError):
                return _error(
                    400,
                    "Group members must contain SCIM User IDs",
                    scim_type="invalidValue",
                )
            current_ids = db.scalars(
                select(SCIMGroupMembership.user_id).where(
                    SCIMGroupMembership.group_id == group.id
                )
            ).all()
            affected_users.update(current_ids)
            if op_name == "add":
                try:
                    combined_ids = set(current_ids) | {
                        UUID(member.value) for member in members
                    }
                except ValueError:
                    return _error(400, "Group member value must be a SCIM User ID")
                members = [
                    SCIMGroupMemberInput(value=str(user_id)) for user_id in combined_ids
                ]
            member_error = _apply_group_members(db, group, members)
            if member_error:
                db.rollback()
                return _error(400, member_error, scim_type="invalidValue")
            affected_users.update(UUID(member.value) for member in members)
        elif path.startswith("members") and op_name == "remove":
            matched = re.fullmatch(r'members\[value\s+eq\s+"([0-9a-fA-F-]{36})"\]', path)
            if matched:
                try:
                    user_id = UUID(matched.group(1))
                except ValueError:
                    return _error(400, "Invalid SCIM User ID in members filter")
                affected_users.add(user_id)
                db.execute(
                    delete(SCIMGroupMembership).where(
                        SCIMGroupMembership.group_id == group.id,
                        SCIMGroupMembership.user_id == user_id,
                    )
                )
            elif path == "members":
                affected_users.update(
                    db.scalars(
                        select(SCIMGroupMembership.user_id).where(
                            SCIMGroupMembership.group_id == group.id
                        )
                    ).all()
                )
                db.execute(
                    delete(SCIMGroupMembership).where(SCIMGroupMembership.group_id == group.id)
                )
            else:
                return _error(400, "Only a members[value eq \"userId\"] filter is supported")
        else:
            return _error(
                400,
                f"Unsupported SCIM Group patch path: {operation.path or '(missing)'}",
            )

    group.updated_at = datetime.now(UTC)
    for user_id in affected_users:
        user = db.get(User, user_id)
        if user:
            _sync_user_groups(db, user)
    record_audit(
        db,
        action="identity.scim.group.updated",
        resource_type="identity_group",
        resource_id=group.id,
        details={"display_name": group.display_name, "member_count": len(affected_users)},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _error(409, "SCIM group displayName already exists")
    db.refresh(group)
    return _group_contract(group, request, db)


@router.put("/Groups/{group_id}", include_in_schema=False)
async def replace_group(group_id: UUID, request: Request, db: Session = Depends(get_db)):
    denied = _authorize(request)
    if denied:
        return denied
    group = db.get(SCIMGroup, group_id)
    if group is None:
        return _error(404, "SCIM group not found")
    payload, error = await _body(request, SCIMGroupCreate)
    if error:
        return error
    if payload.external_id and payload.external_id != group.external_id:
        other_group = db.scalar(
            select(SCIMGroup).where(
                SCIMGroup.external_id == payload.external_id,
                SCIMGroup.id != group.id,
            )
        )
        if other_group:
            return _error(409, "SCIM group externalId already exists")
    previous_ids = set(
        db.scalars(
            select(SCIMGroupMembership.user_id).where(
                SCIMGroupMembership.group_id == group.id
            )
        ).all()
    )
    member_error = _apply_group_members(db, group, payload.members)
    if member_error:
        db.rollback()
        return _error(400, member_error, scim_type="invalidValue")
    current_ids = set(
        db.scalars(
            select(SCIMGroupMembership.user_id).where(
                SCIMGroupMembership.group_id == group.id
            )
        ).all()
    )
    group.display_name = payload.display_name.strip()
    group.external_id = payload.external_id
    group.updated_at = datetime.now(UTC)
    for user_id in previous_ids | current_ids:
        user = db.get(User, user_id)
        if user:
            _sync_user_groups(db, user)
    record_audit(
        db,
        action="identity.scim.group.replaced",
        resource_type="identity_group",
        resource_id=group.id,
        details={"display_name": group.display_name, "member_count": len(current_ids)},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _error(409, "SCIM group displayName or externalId already exists")
    db.refresh(group)
    return _group_contract(group, request, db)


@router.delete("/Groups/{group_id}", status_code=204, include_in_schema=False)
def delete_group(group_id: UUID, request: Request, db: Session = Depends(get_db)):
    denied = _authorize(request)
    if denied:
        return denied
    group = db.get(SCIMGroup, group_id)
    if group is None:
        return _error(404, "SCIM group not found")
    user_ids = db.scalars(
        select(SCIMGroupMembership.user_id).where(SCIMGroupMembership.group_id == group.id)
    ).all()
    record_audit(
        db,
        action="identity.scim.group.deprovisioned",
        resource_type="identity_group",
        resource_id=group.id,
        details={"display_name": group.display_name, "member_count": len(user_ids)},
    )
    db.delete(group)
    db.flush()
    for user_id in user_ids:
        user = db.get(User, user_id)
        if user:
            _sync_user_groups(db, user)
    db.commit()
    return Response(status_code=204)
