#!/usr/bin/env python3
"""Pin the staging Kustomize image to the exact digest produced by CI."""

from __future__ import annotations

import re
import sys
from pathlib import Path

IMAGE_NAME = "deployforge-demo-service"
IMAGE_PATTERN = re.compile(
    r"^ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+-demo-service$"
)
DIGEST_PATTERN = re.compile(
    r"^(\s*digest:\s*)sha256:[0-9a-f]{64}(\s*(?:#.*)?)(\r?\n?)$"
)
ENTRY_PATTERN = re.compile(r"^(\s*)-\s*name:\s*(\S+)\s*(?:#.*)?$")


def update_digest(path: Path, image: str, digest: str) -> None:
    if not IMAGE_PATTERN.fullmatch(image):
        raise ValueError("Image must be the expected immutable GHCR repository name")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Image digest must be a lowercase SHA-256 digest")

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    target_entries = [
        index
        for index, line in enumerate(lines)
        if (match := ENTRY_PATTERN.match(line)) and match.group(2) == IMAGE_NAME
    ]
    if len(target_entries) != 1:
        raise ValueError(f"Expected exactly one Kustomize image named {IMAGE_NAME}")

    start = target_entries[0]
    entry_match = ENTRY_PATTERN.match(lines[start])
    assert entry_match is not None
    entry_indent = len(entry_match.group(1))
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = ENTRY_PATTERN.match(lines[index])
        if match and len(match.group(1)) <= entry_indent:
            end = index
            break

    image_lines = [line for line in lines[start:end] if line.strip() == f"newName: {image}"]
    if len(image_lines) != 1:
        raise ValueError("The staging overlay does not target the built image repository")

    digest_lines = [
        index for index in range(start + 1, end) if DIGEST_PATTERN.match(lines[index])
    ]
    if len(digest_lines) != 1:
        raise ValueError("Expected exactly one SHA-256 digest in the target image entry")

    index = digest_lines[0]
    match = DIGEST_PATTERN.match(lines[index])
    assert match is not None
    lines[index] = f"{match.group(1)}{digest}{match.group(2)}{match.group(3)}"
    path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "Usage: update-staging-digest.py <kustomization.yaml> <ghcr-image> <sha256:digest>",
            file=sys.stderr,
        )
        return 2
    try:
        update_digest(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
    except (OSError, ValueError) as exc:
        print(f"Could not prepare staging GitOps revision: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
