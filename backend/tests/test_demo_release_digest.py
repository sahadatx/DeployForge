import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPDATE_SCRIPT = PROJECT_ROOT / "examples/demo-service/scripts/update-staging-digest.py"
IMAGE = "ghcr.io/sahadatx/deployforge-demo-service"
CALLBACK_SCRIPT = PROJECT_ROOT / "examples/demo-service/scripts/report-build.py"


def test_update_staging_digest_pins_only_the_expected_image(tmp_path):
    manifest = tmp_path / "kustomization.yaml"
    manifest.write_text(
        """apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
images:
  - name: deployforge-demo-service
    newName: ghcr.io/sahadatx/deployforge-demo-service
    digest: sha256:0000000000000000000000000000000000000000000000000000000000000000
  - name: unrelated
    newName: ghcr.io/sahadatx/unrelated
    newTag: stable
""",
        encoding="utf-8",
    )
    digest = "sha256:" + "a" * 64

    result = subprocess.run(
        [sys.executable, str(UPDATE_SCRIPT), str(manifest), IMAGE, digest],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    updated = manifest.read_text(encoding="utf-8")
    assert f"    digest: {digest}\n" in updated
    assert "    newTag: stable\n" in updated


def test_update_staging_digest_fails_closed_for_invalid_input(tmp_path):
    manifest = tmp_path / "kustomization.yaml"
    original = (
        "images:\n"
        "  - name: deployforge-demo-service\n"
        f"    newName: {IMAGE}\n"
        "    digest: sha256:" + "0" * 64 + "\n"
    )
    manifest.write_text(original, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(UPDATE_SCRIPT), str(manifest), IMAGE, "latest"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert manifest.read_text(encoding="utf-8") == original
    assert "SHA-256 digest" in result.stderr


def test_release_callback_is_restricted_to_the_configured_host():
    build_run_id = str(uuid4())
    env = {
        **os.environ,
        "BUILD_RUN_ID": build_run_id,
        "DEPLOYFORGE_CALLBACK_URL": (
            f"https://deployforge.example.test/api/v1/build-runs/{build_run_id}/callback"
        ),
        "DEPLOYFORGE_CALLBACK_ALLOWED_HOST": "deployforge.example.test",
    }
    command = [sys.executable, str(CALLBACK_SCRIPT), "--validate-callback"]

    accepted = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    rejected = subprocess.run(
        command,
        env={
            **env,
            "DEPLOYFORGE_CALLBACK_URL": env["DEPLOYFORGE_CALLBACK_URL"].replace(
                "deployforge.example.test", "attacker.example.test"
            ),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert accepted.returncode == 0, accepted.stderr
    assert rejected.returncode == 1
    assert "allowlisted HTTPS host" in rejected.stderr
