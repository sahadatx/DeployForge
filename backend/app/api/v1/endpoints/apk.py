from __future__ import annotations

import re
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_csrf
from app.core.audit import record_audit
from app.core.authorization import require_workspace_access
from app.core.config import settings
from app.models import User, UserSession

router = APIRouter()
APK_OPERATOR_ROLES = {"workspace_admin", "platform_engineer", "developer"}
MAX_APK_BYTES = 64 * 1024 * 1024
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]")


class UploadTooLarge(Exception):
    pass


@router.post(
    "/workspaces/{workspace_id}/apk/inspect",
    summary="Inspect an APK in the isolated parser service",
)
async def inspect_apk(
    workspace_id: UUID,
    request: Request,
    artifact_name: str = Header(default="uploaded.apk", alias="X-Artifact-Name", max_length=255),
    identity: tuple[User, UserSession] = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    actor = identity[0]
    require_workspace_access(db, actor, workspace_id, roles=APK_OPERATOR_ROLES)
    if not settings.apk_parser_url:
        raise HTTPException(status_code=503, detail="Isolated APK parser is not configured")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"application/vnd.android.package-archive", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Upload must be an APK binary stream")
    declared_length = request.headers.get("content-length")
    if declared_length and declared_length.isdigit() and int(declared_length) > MAX_APK_BYTES:
        raise HTTPException(status_code=413, detail="APK exceeds the 64 MiB upload limit")

    filename = _SAFE_FILENAME.sub("_", artifact_name.replace("\\", "/").split("/")[-1])
    filename = filename.strip(" .")[:180] or "uploaded.apk"
    bytes_seen = 0
    too_large = False

    async def body_stream():
        nonlocal bytes_seen, too_large
        async for chunk in request.stream():
            bytes_seen += len(chunk)
            if bytes_seen > MAX_APK_BYTES:
                too_large = True
                raise UploadTooLarge
            yield chunk

    timeout = httpx.Timeout(settings.apk_parser_timeout_seconds, connect=3.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(
                f"{settings.apk_parser_url}/api/v1/parse",
                content=body_stream(),
                headers={
                    "Content-Type": "application/vnd.android.package-archive",
                    "X-Artifact-Name": filename,
                },
            )
    except UploadTooLarge as exc:
        raise HTTPException(
            status_code=413, detail="APK exceeds the 64 MiB upload limit"
        ) from exc
    except httpx.RequestError as exc:
        if too_large:
            raise HTTPException(
                status_code=413, detail="APK exceeds the 64 MiB upload limit"
            ) from exc
        raise HTTPException(
            status_code=503,
            detail="Isolated APK parser is temporarily unavailable",
            headers={"Retry-After": "10"},
        ) from exc

    if response.status_code >= 400:
        detail = "APK could not be parsed"
        try:
            upstream_detail = response.json().get("detail")
            if isinstance(upstream_detail, str):
                detail = upstream_detail[:300]
        except (ValueError, AttributeError):
            pass
        raise HTTPException(status_code=response.status_code, detail=detail)
    try:
        result = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="APK parser returned invalid JSON") from exc
    if not isinstance(result, dict) or not isinstance(result.get("sha256"), str):
        raise HTTPException(status_code=502, detail="APK parser returned an invalid report")

    record_audit(
        db,
        action="artifact.apk.inspected",
        resource_type="apk_artifact",
        resource_id=result["sha256"],
        workspace_id=workspace_id,
        actor=actor,
        details={
            "artifact_name": filename,
            "size_bytes": result.get("size_bytes"),
            "package_name": result.get("package_name"),
        },
    )
    db.commit()
    return result
