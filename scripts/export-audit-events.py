#!/usr/bin/env python3
"""Export audit events through durable, chained batches to immutable S3 storage."""

import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.models import (
    AuditEvent,
    AuditExportBatch,
    AuditExportBatchEvent,
    AuditExportCursor,
)

EXPORT_NAME = "s3-worm-v1"
ADVISORY_LOCK_KEY = 6_102_141_760_083_784
EMPTY_CHAIN_HASH = "0" * 64


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _event_record(event: AuditEvent) -> dict[str, object]:
    created_at = event.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return {
        "id": str(event.id),
        "workspace_id": str(event.workspace_id) if event.workspace_id else None,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "outcome": event.outcome,
        "details": event.details or {},
        "created_at": created_at.astimezone(UTC).isoformat(),
    }


def _chain_records(
    records: list[dict[str, object]], previous_hash: str
) -> tuple[bytes, str]:
    envelopes = []
    for record in records:
        event_hash = hashlib.sha256(
            bytes.fromhex(previous_hash) + _canonical(record)
        ).hexdigest()
        envelopes.append(
            {"event": record, "previous_hash": previous_hash, "event_hash": event_hash}
        )
        previous_hash = event_hash
    payload = b"".join(_canonical(envelope) + b"\n" for envelope in envelopes)
    return payload, previous_hash


def _put_immutable(s3, *, bucket: str, key: str, body: bytes, content_type: str) -> None:
    retention_days = int(os.getenv("AUDIT_EXPORT_RETENTION_DAYS", "365"))
    if retention_days < 30:
        raise ValueError("AUDIT_EXPORT_RETENTION_DAYS must be at least 30")
    object_lock_mode = os.getenv("AUDIT_EXPORT_OBJECT_LOCK_MODE", "COMPLIANCE").upper()
    if object_lock_mode not in {"COMPLIANCE", "GOVERNANCE"}:
        raise ValueError("AUDIT_EXPORT_OBJECT_LOCK_MODE must be COMPLIANCE or GOVERNANCE")
    retention_until = datetime.now(UTC) + timedelta(days=retention_days)
    args = {
        "Bucket": bucket,
        "Key": key,
        "Body": body,
        "ContentType": content_type,
        "ChecksumAlgorithm": "SHA256",
        "ObjectLockMode": object_lock_mode,
        "ObjectLockRetainUntilDate": retention_until,
        "ServerSideEncryption": "aws:kms" if os.getenv("AUDIT_EXPORT_KMS_KEY_ID") else "AES256",
    }
    kms_key_id = os.getenv("AUDIT_EXPORT_KMS_KEY_ID")
    if kms_key_id:
        args["SSEKMSKeyId"] = kms_key_id
    s3.put_object(**args)


def _reserve_or_load_batch(
    db: Session, *, prefix: str, batch_size: int
) -> AuditExportBatch | None:
    pending = db.scalar(
        select(AuditExportBatch)
        .where(AuditExportBatch.export_name == EXPORT_NAME, AuditExportBatch.status == "pending")
        .order_by(AuditExportBatch.sequence)
        .limit(1)
    )
    if pending is not None:
        return pending

    events = list(
        db.scalars(
            select(AuditEvent)
            .outerjoin(
                AuditExportBatchEvent,
                AuditExportBatchEvent.event_id == AuditEvent.id,
            )
            .where(AuditExportBatchEvent.event_id.is_(None))
            .order_by(AuditEvent.created_at, AuditEvent.id)
            .limit(batch_size)
        ).all()
    )
    if not events:
        return None

    cursor = db.get(AuditExportCursor, EXPORT_NAME)
    if cursor is None:
        cursor = AuditExportCursor(export_name=EXPORT_NAME, chain_hash=EMPTY_CHAIN_HASH)
        db.add(cursor)
        db.flush()

    records = [_event_record(event) for event in events]
    previous_hash = cursor.chain_hash or EMPTY_CHAIN_HASH
    payload, final_hash = _chain_records(records, previous_hash)
    sequence = cursor.batch_sequence + 1
    batch_id = uuid4()
    created_at = datetime.fromisoformat(str(records[-1]["created_at"]))
    dated_prefix = f"{prefix}/{created_at:%Y/%m/%d}" if prefix else f"{created_at:%Y/%m/%d}"
    object_stem = f"{dated_prefix}/audit-{sequence:012d}-{batch_id.hex}"
    if len(object_stem.encode("utf-8")) > 1024:
        raise ValueError("AUDIT_EXPORT_PREFIX makes the S3 object key exceed 1024 bytes")

    batch = AuditExportBatch(
        id=batch_id,
        export_name=EXPORT_NAME,
        sequence=sequence,
        object_stem=object_stem,
        event_count=len(events),
        previous_chain_hash=previous_hash,
        final_chain_hash=final_hash,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        status="pending",
    )
    db.add(batch)
    db.flush()
    db.add_all(
        AuditExportBatchEvent(
            event_id=event.id,
            batch_id=batch.id,
            ordinal=ordinal,
            record=record,
        )
        for ordinal, (event, record) in enumerate(zip(events, records, strict=True))
    )
    db.commit()
    db.refresh(batch)
    return batch


def _batch_objects(db: Session, batch: AuditExportBatch) -> tuple[bytes, bytes]:
    rows = db.scalars(
        select(AuditExportBatchEvent)
        .where(AuditExportBatchEvent.batch_id == batch.id)
        .order_by(AuditExportBatchEvent.ordinal)
    ).all()
    if len(rows) != batch.event_count or [row.ordinal for row in rows] != list(
        range(batch.event_count)
    ):
        raise RuntimeError("Reserved audit batch is incomplete or has invalid event ordering")

    records = [row.record for row in rows]
    payload, final_hash = _chain_records(records, batch.previous_chain_hash)
    payload_hash = hashlib.sha256(payload).hexdigest()
    if final_hash != batch.final_chain_hash or payload_hash != batch.payload_sha256:
        raise RuntimeError("Reserved audit batch no longer matches its verified hashes")

    first = records[0]
    last = records[-1]
    manifest = {
        "format": "deployforge-audit-chain-v1",
        "batch_sequence": batch.sequence,
        "batch_id": str(batch.id),
        "count": batch.event_count,
        "first_event_id": first["id"],
        "last_event_id": last["id"],
        "first_created_at": first["created_at"],
        "last_created_at": last["created_at"],
        "previous_chain_hash": batch.previous_chain_hash,
        "final_chain_hash": batch.final_chain_hash,
        "payload_sha256": batch.payload_sha256,
    }
    return payload, _canonical(manifest) + b"\n"


def _export_locked() -> int:
    import boto3

    bucket = os.environ["AUDIT_EXPORT_BUCKET"]
    prefix = os.getenv("AUDIT_EXPORT_PREFIX", "deployforge/audit").strip("/")
    batch_size = int(os.getenv("AUDIT_EXPORT_BATCH_SIZE", "5000"))
    if not 1 <= batch_size <= 20_000:
        raise ValueError("AUDIT_EXPORT_BATCH_SIZE must be between 1 and 20000")
    if len(prefix.encode("utf-8")) > 900:
        raise ValueError("AUDIT_EXPORT_PREFIX must not exceed 900 UTF-8 bytes")

    with SessionLocal() as db:
        batch = _reserve_or_load_batch(db, prefix=prefix, batch_size=batch_size)
        if batch is None:
            return 0
        payload, manifest = _batch_objects(db, batch)
        object_stem = batch.object_stem
        batch_id = batch.id
        batch_sequence = batch.sequence
        event_count = batch.event_count
        final_hash = batch.final_chain_hash
        previous_hash = batch.previous_chain_hash
        rows = db.scalars(
            select(AuditExportBatchEvent)
            .where(AuditExportBatchEvent.batch_id == batch.id)
            .order_by(AuditExportBatchEvent.ordinal)
        ).all()
        last_record = rows[-1].record

    client = boto3.client(
        "s3",
        region_name=os.getenv("AUDIT_EXPORT_REGION"),
        endpoint_url=os.getenv("AUDIT_EXPORT_ENDPOINT_URL") or None,
    )
    _put_immutable(
        client,
        bucket=bucket,
        key=f"{object_stem}.jsonl",
        body=payload,
        content_type="application/x-ndjson",
    )
    _put_immutable(
        client,
        bucket=bucket,
        key=f"{object_stem}.manifest.json",
        body=manifest,
        content_type="application/json",
    )

    with SessionLocal() as db:
        batch = db.get(AuditExportBatch, batch_id)
        cursor = db.get(AuditExportCursor, EXPORT_NAME)
        if batch is None or cursor is None or batch.status != "pending":
            raise RuntimeError("Reserved audit batch or cursor changed during export")
        if (
            cursor.batch_sequence != batch_sequence - 1
            or cursor.chain_hash != previous_hash
        ):
            raise RuntimeError("Audit export cursor moved while a batch was pending")
        cursor.cursor_created_at = datetime.fromisoformat(str(last_record["created_at"]))
        cursor.cursor_event_id = UUID(str(last_record["id"]))
        cursor.chain_hash = final_hash
        cursor.batch_sequence = batch_sequence
        cursor.updated_at = datetime.now(UTC)
        batch.status = "exported"
        batch.exported_at = datetime.now(UTC)
        db.commit()

    print(
        json.dumps(
            {
                "batch_sequence": batch_sequence,
                "exported": event_count,
                "batch_id": str(batch_id),
                "final_chain_hash": final_hash,
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
            },
            sort_keys=True,
        )
    )
    return event_count


def export_once() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Immutable audit export requires PostgreSQL advisory locks")

    with engine.connect() as lock_connection:
        acquired = lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_key)"), {"lock_key": ADVISORY_LOCK_KEY}
        )
        if not acquired:
            print("Another audit exporter owns the advisory lock; skipping this run.")
            return 0
        try:
            return _export_locked()
        finally:
            lock_connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": ADVISORY_LOCK_KEY}
            )
            lock_connection.commit()


if __name__ == "__main__":
    try:
        export_once()
    except Exception as exc:
        print(f"Audit export failed: {type(exc).__name__}", file=sys.stderr)
        raise
