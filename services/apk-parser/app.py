from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import tempfile
import zipfile
from pathlib import Path

from androguard.core.apk import APK
from fastapi import FastAPI, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger("deployforge.apk_parser")
app = FastAPI(title="DeployForge Isolated APK Parser", version="0.2.0")
MAX_APK_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 100_000
MAX_ARCHIVE_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_LIST_ITEMS = 2_000
_parse_slots = asyncio.Semaphore(2)


class InvalidAPK(ValueError):
    pass


def _clean_filename(value: str) -> str:
    name = Path(value.replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    return (name or "uploaded.apk")[:180]


def _certificate_fingerprint(certificate: object) -> str | None:
    fingerprint = getattr(certificate, "sha256", None)
    if isinstance(fingerprint, bytes):
        return fingerprint.hex()
    if isinstance(fingerprint, str):
        return fingerprint.replace(":", "").lower()
    return None


def _parse_file(path: str, artifact_name: str, size_bytes: int, sha256: str) -> dict:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise InvalidAPK("APK contains too many archive entries")
            if sum(entry.file_size for entry in entries) > MAX_ARCHIVE_EXPANDED_BYTES:
                raise InvalidAPK("APK expands beyond the parser limit")
            manifests = [entry for entry in entries if entry.filename == "AndroidManifest.xml"]
            if len(manifests) != 1 or manifests[0].file_size > MAX_MANIFEST_BYTES:
                raise InvalidAPK("APK has a missing, duplicate, or oversized Android manifest")
            dex_count = sum(
                1 for entry in entries if re.fullmatch(r"classes(?:\d+)?\.dex", entry.filename)
            )
            native_abis = sorted(
                {
                    parts[1]
                    for entry in entries
                    if len(parts := entry.filename.split("/")) >= 3
                    and parts[0] == "lib"
                    and parts[2].endswith(".so")
                }
            )
    except InvalidAPK:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
        raise InvalidAPK("Upload is not a readable APK archive") from exc

    try:
        apk = APK(path)
        if not apk.is_valid_APK():
            raise InvalidAPK("Android manifest could not be parsed")
        package_name = str(apk.get_package() or "").strip()
        if not package_name:
            raise InvalidAPK("APK manifest does not contain a package name")

        signer_fingerprints: set[str] = set()
        for getter_name in ("get_certificates_v1", "get_certificates_v2", "get_certificates_v3"):
            getter = getattr(apk, getter_name)
            for certificate in getter():
                fingerprint = _certificate_fingerprint(certificate)
                if fingerprint:
                    signer_fingerprints.add(fingerprint)
        signing_schemes = {
            scheme
            for scheme, getter_name in (
                ("v1", "is_signed_v1"),
                ("v2", "is_signed_v2"),
                ("v3", "is_signed_v3"),
            )
            if bool(getattr(apk, getter_name)())
        }
        try:
            app_name = apk.get_app_name()
        except Exception:
            app_name = None

        return {
            "artifact_name": artifact_name,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "package_name": package_name,
            "app_name": str(app_name)[:300] if app_name else None,
            "version_name": str(apk.get_androidversion_name() or "")[:180] or None,
            "version_code": str(apk.get_androidversion_code() or "")[:80] or None,
            "min_sdk": str(apk.get_min_sdk_version() or "")[:40] or None,
            "target_sdk": str(apk.get_target_sdk_version() or "")[:40] or None,
            "max_sdk": str(apk.get_max_sdk_version() or "")[:40] or None,
            "permissions": sorted(set(apk.get_permissions()))[:MAX_LIST_ITEMS],
            "activities": sorted(set(apk.get_activities()))[:MAX_LIST_ITEMS],
            "services": sorted(set(apk.get_services()))[:MAX_LIST_ITEMS],
            "receivers": sorted(set(apk.get_receivers()))[:MAX_LIST_ITEMS],
            "providers": sorted(set(apk.get_providers()))[:MAX_LIST_ITEMS],
            "signing_schemes_found": sorted(signing_schemes),
            "signer_certificate_sha256": sorted(signer_fingerprints),
            "signature_validation": "not_performed",
            "archive_entry_count": len(entries),
            "dex_file_count": dex_count,
            "native_abis": native_abis,
        }
    except InvalidAPK:
        raise
    except Exception as exc:
        logger.info("apk_parse_rejected", extra={"reason": type(exc).__name__})
        raise InvalidAPK("APK metadata could not be safely parsed") from exc


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "service": "apk-parser"}


@app.post("/api/v1/parse")
async def parse_apk(request: Request) -> dict:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"application/vnd.android.package-archive", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Upload must be an APK binary stream")
    declared_length = request.headers.get("content-length")
    if declared_length and declared_length.isdigit() and int(declared_length) > MAX_APK_BYTES:
        raise HTTPException(status_code=413, detail="APK exceeds the 64 MiB upload limit")

    artifact_name = _clean_filename(request.headers.get("x-artifact-name", "uploaded.apk"))
    size_bytes = 0
    digest = hashlib.sha256()
    try:
        with tempfile.NamedTemporaryFile(prefix="deployforge-apk-", suffix=".apk") as apk_file:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size_bytes += len(chunk)
                if size_bytes > MAX_APK_BYTES:
                    raise HTTPException(
                        status_code=413, detail="APK exceeds the 64 MiB upload limit"
                    )
                digest.update(chunk)
                apk_file.write(chunk)
            if size_bytes == 0:
                raise HTTPException(status_code=400, detail="APK upload is empty")
            apk_file.flush()

            try:
                await asyncio.wait_for(_parse_slots.acquire(), timeout=2)
            except TimeoutError as exc:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="APK parser is at capacity; retry shortly",
                    headers={"Retry-After": "5"},
                ) from exc
            try:
                return await run_in_threadpool(
                    _parse_file,
                    apk_file.name,
                    artifact_name,
                    size_bytes,
                    digest.hexdigest(),
                )
            finally:
                _parse_slots.release()
    except HTTPException:
        raise
    except InvalidAPK as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("apk_upload_failed")
        raise HTTPException(status_code=400, detail="APK could not be processed") from exc
