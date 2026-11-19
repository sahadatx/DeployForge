import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.models import AuditEvent, AuditExportBatchEvent, AuditExportCursor

ROOT = Path(__file__).resolve().parents[2]


def _load_exporter():
    exporter_path = ROOT / "scripts" / "export-audit-events.py"
    spec = importlib.util.spec_from_file_location("deployforge_audit_export", exporter_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _audit_event(action: str, created_at: datetime) -> AuditEvent:
    return AuditEvent(
        id=uuid4(),
        actor_type="system",
        action=action,
        resource_type="audit_export_test",
        resource_id=action,
        outcome="succeeded",
        details={"action": action},
        created_at=created_at,
    )


def test_export_reservation_resumes_and_captures_late_commits(client):
    exporter = _load_exporter()
    session_factory = client.app.state.test_session_factory
    now = datetime.now(UTC)
    first_event = _audit_event("first", now + timedelta(seconds=1))
    with session_factory() as db:
        db.add(first_event)
        db.commit()

        batch = exporter._reserve_or_load_batch(db, prefix="audit-test", batch_size=1)
        assert batch is not None
        assert batch.sequence == 1
        assert batch.status == "pending"
        first_batch_id = batch.id
        first_batch_hash = batch.final_chain_hash

    late_event = _audit_event("late-commit", now - timedelta(seconds=1))
    with session_factory() as db:
        db.add(late_event)
        db.commit()

        resumed = exporter._reserve_or_load_batch(db, prefix="audit-test", batch_size=1)
        assert resumed is not None
        assert resumed.id == first_batch_id
        assert resumed.sequence == 1

        cursor = db.get(AuditExportCursor, exporter.EXPORT_NAME)
        assert cursor is not None
        first_record = db.scalar(
            select(AuditExportBatchEvent.record).where(
                AuditExportBatchEvent.batch_id == resumed.id
            )
        )
        cursor.cursor_created_at = datetime.fromisoformat(first_record["created_at"])
        cursor.cursor_event_id = first_event.id
        cursor.chain_hash = first_batch_hash
        cursor.batch_sequence = 1
        resumed.status = "exported"
        resumed.exported_at = now
        db.commit()

        next_batch = exporter._reserve_or_load_batch(db, prefix="audit-test", batch_size=10)
        assert next_batch is not None
        assert next_batch.sequence == 2
        assert next_batch.previous_chain_hash == first_batch_hash
        reserved_event = db.scalar(
            select(AuditExportBatchEvent).where(
                AuditExportBatchEvent.batch_id == next_batch.id
            )
        )
        assert reserved_event is not None
        assert reserved_event.event_id == late_event.id


def _write_manifest_pair(
    directory: Path, sequence: int, previous_hash: str
) -> tuple[Path, Path, str]:
    event = {
        "id": str(uuid4()),
        "action": f"event-{sequence}",
        "created_at": datetime.now(UTC).isoformat(),
    }
    def canonical(value):
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    event_hash = hashlib.sha256(bytes.fromhex(previous_hash) + canonical(event)).hexdigest()
    payload = canonical(
        {"event": event, "previous_hash": previous_hash, "event_hash": event_hash}
    ) + b"\n"
    manifest = {
        "format": "deployforge-audit-chain-v1",
        "batch_sequence": sequence,
        "count": 1,
        "first_event_id": event["id"],
        "last_event_id": event["id"],
        "previous_chain_hash": previous_hash,
        "final_chain_hash": event_hash,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    payload_path = directory / f"batch-{sequence}.jsonl"
    manifest_path = directory / f"batch-{sequence}.manifest.json"
    payload_path.write_bytes(payload)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return payload_path, manifest_path, event_hash


def test_audit_export_verifier_checks_continuity_across_manifests(tmp_path):
    verifier = ROOT / "scripts" / "verify-audit-export.py"
    empty_hash = "0" * 64
    first_payload, first_manifest, first_hash = _write_manifest_pair(tmp_path, 1, empty_hash)
    second_payload, second_manifest, _ = _write_manifest_pair(tmp_path, 2, first_hash)
    command = [
        sys.executable,
        str(verifier),
        str(first_payload),
        str(first_manifest),
        str(second_payload),
        str(second_manifest),
    ]
    verified = subprocess.run(command, capture_output=True, text=True, check=False)
    assert verified.returncode == 0, verified.stderr
    assert "Verified 2 audit events across 2 batch(es)" in verified.stdout

    second_manifest_data = json.loads(second_manifest.read_text(encoding="utf-8"))
    second_envelope = json.loads(second_payload.read_text(encoding="utf-8"))
    empty_hash = "0" * 64
    second_envelope["previous_hash"] = empty_hash
    second_envelope["event_hash"] = hashlib.sha256(
        bytes.fromhex(empty_hash)
        + json.dumps(
            second_envelope["event"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    tampered_payload = json.dumps(
        second_envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8") + b"\n"
    second_payload.write_bytes(tampered_payload)
    second_manifest_data["previous_chain_hash"] = empty_hash
    second_manifest_data["final_chain_hash"] = second_envelope["event_hash"]
    second_manifest_data["payload_sha256"] = hashlib.sha256(tampered_payload).hexdigest()
    second_manifest.write_text(json.dumps(second_manifest_data), encoding="utf-8")
    rejected = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rejected.returncode != 0
    assert "previous chain hash does not match" in rejected.stderr
