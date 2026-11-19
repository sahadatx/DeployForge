#!/usr/bin/env python3
"""Create a compact, digest-bound predicate for a Grype JSON report."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SEVERITIES = ("critical", "high", "medium", "low", "unknown")


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: prepare-grype-attestation.py REPORT.json OUTPUT.json")
    report_path, output_path = map(Path, sys.argv[1:])
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    if not isinstance(report, dict):
        raise ValueError("Grype report must be a JSON object")
    descriptor = report.get("descriptor")
    if not isinstance(descriptor, dict) or str(descriptor.get("name", "")).casefold() != "grype":
        raise ValueError("Vulnerability report was not produced by Grype")
    version = str(descriptor.get("version", "")).strip()
    if not version or len(version) > 100:
        raise ValueError("Grype version is missing or invalid")
    matches = report.get("matches", [])
    if not isinstance(matches, list):
        raise ValueError("Grype report matches must be a list")
    summary = dict.fromkeys(SEVERITIES, 0)
    for match in matches:
        vulnerability = match.get("vulnerability", {}) if isinstance(match, dict) else {}
        severity = str(vulnerability.get("severity", "unknown")).casefold()
        if severity == "negligible":
            severity = "low"
        if severity not in summary:
            severity = "unknown"
        summary[severity] += 1
    predicate = {
        "schemaVersion": 1,
        "scanner": "grype",
        "scannerVersion": version,
        "reportDigest": "sha256:" + hashlib.sha256(report_bytes).hexdigest(),
        "summary": summary,
    }
    output_path.write_text(
        json.dumps(predicate, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
