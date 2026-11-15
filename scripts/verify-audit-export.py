#!/usr/bin/env python3
"""Verify one or more exported audit JSONL/manifest pairs and chain continuity."""

import argparse
import hashlib
import json
import re
from pathlib import Path

HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def verify_pair(payload_path: Path, manifest_path: Path) -> tuple[dict, int]:
    payload = payload_path.read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") != "deployforge-audit-chain-v1":
        raise ValueError(f"{manifest_path}: unsupported audit manifest format")
    actual_payload_hash = hashlib.sha256(payload).hexdigest()
    if actual_payload_hash != manifest.get("payload_sha256"):
        raise ValueError(f"{payload_path}: payload checksum does not match its manifest")

    previous_hash = manifest.get("previous_chain_hash")
    if not isinstance(previous_hash, str) or not HASH_PATTERN.fullmatch(previous_hash):
        raise ValueError(f"{manifest_path}: invalid previous chain hash")
    lines = [line for line in payload.splitlines() if line]
    if not lines or len(lines) != manifest.get("count"):
        raise ValueError(f"{payload_path}: event count does not match its manifest")

    event_ids = []
    for line_number, line in enumerate(lines, 1):
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{payload_path}:{line_number}: invalid JSON envelope") from exc
        if not isinstance(envelope, dict) or envelope.get("previous_hash") != previous_hash:
            raise ValueError(f"{payload_path}:{line_number}: audit hash chain is discontinuous")
        event = envelope.get("event")
        if not isinstance(event, dict):
            raise ValueError(f"{payload_path}:{line_number}: event is not an object")
        expected_hash = hashlib.sha256(bytes.fromhex(previous_hash) + canonical(event)).hexdigest()
        if envelope.get("event_hash") != expected_hash:
            raise ValueError(f"{payload_path}:{line_number}: audit event hash does not verify")
        event_id = event.get("id")
        if not isinstance(event_id, str):
            raise ValueError(f"{payload_path}:{line_number}: audit event has no ID")
        event_ids.append(event_id)
        previous_hash = expected_hash

    if previous_hash != manifest.get("final_chain_hash"):
        raise ValueError(f"{manifest_path}: final audit chain hash does not match")
    if (
        manifest.get("first_event_id") != event_ids[0]
        or manifest.get("last_event_id") != event_ids[-1]
    ):
        raise ValueError(f"{manifest_path}: event ID range does not match the payload")
    sequence = manifest.get("batch_sequence")
    if sequence is not None and (
        isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1
    ):
        raise ValueError(f"{manifest_path}: invalid batch sequence")
    return manifest, len(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify audit payload/manifest pairs in export order; pass multiple pairs "
            "to verify continuity."
        )
    )
    parser.add_argument(
        "files",
        type=Path,
        nargs="+",
        metavar="FILE",
        help="one or more payload.jsonl manifest.json pairs, ordered by export sequence",
    )
    parser.add_argument(
        "--expected-previous-hash",
        help="optional chain hash expected before the first supplied batch",
    )
    args = parser.parse_args()
    if len(args.files) % 2:
        parser.error("supply each JSONL payload with its manifest as a pair")
    if args.expected_previous_hash and not HASH_PATTERN.fullmatch(args.expected_previous_hash):
        parser.error("--expected-previous-hash must be a lowercase SHA-256 hex digest")

    expected_previous = args.expected_previous_hash
    expected_sequence = None
    total_events = 0
    pair_count = len(args.files) // 2
    for pair_index in range(pair_count):
        payload_path, manifest_path = args.files[pair_index * 2 : pair_index * 2 + 2]
        manifest, event_count = verify_pair(payload_path, manifest_path)
        if expected_previous is not None and manifest["previous_chain_hash"] != expected_previous:
            raise SystemExit(f"{manifest_path}: previous chain hash does not match prior batch")
        sequence = manifest.get("batch_sequence")
        if sequence is not None and expected_sequence is not None and sequence != expected_sequence:
            raise SystemExit(f"{manifest_path}: audit batch sequence is discontinuous")
        if sequence is not None:
            expected_sequence = sequence + 1
        expected_previous = manifest["final_chain_hash"]
        total_events += event_count

    print(
        f"Verified {total_events} audit events across {pair_count} batch(es); "
        f"final chain hash {expected_previous}"
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"Audit export verification failed: {exc}") from exc
