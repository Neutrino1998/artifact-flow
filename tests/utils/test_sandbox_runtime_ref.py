import importlib.util
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "sandbox_runtime_ref.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sandbox_runtime_ref", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_slug_is_deterministic_and_content_sensitive(tmp_path):
    module = _load_module()
    for relative in module.RUNTIME_INPUTS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture:{relative}\n", encoding="utf-8")

    first = module.runtime_slug(tmp_path)
    assert first == module.runtime_slug(tmp_path)
    changed = tmp_path / module.RUNTIME_INPUTS[0]
    changed.write_text("changed\n", encoding="utf-8")
    assert module.runtime_slug(tmp_path) != first


def test_runtime_ref_cli_and_release_wiring_use_exact_tag():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--arch", "amd64"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    image_ref = result.stdout.strip()
    assert re.fullmatch(r"artifactflow-sandbox:[0-9a-f]{16}-amd64", image_ref)

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    release = (ROOT / "scripts" / "release.sh").read_text(encoding="utf-8")
    fleet = (ROOT / "deploy" / "scripts" / "fleet.sh").read_text(encoding="utf-8")
    assert "ARG ARTIFACTFLOW_SANDBOX_IMAGE=artifactflow-sandbox:latest" in dockerfile
    assert '--build-arg "ARTIFACTFLOW_SANDBOX_IMAGE=${SANDBOX_IMAGE_REF}"' in release
    assert "Sandbox image required: ${SANDBOX_IMAGE_REF}" in release
    assert 'docker image inspect "$BUNDLE_SANDBOX_IMAGE"' in fleet


def test_runtime_inputs_cover_every_file_copied_into_the_image():
    module = _load_module()
    inputs = set(module.RUNTIME_INPUTS)
    assert {
        "sandbox/Dockerfile",
        "sandbox/requirements.txt",
        "sandbox/office_cli.py",
        "sandbox/text_edit.py",
        "src/utils/text_match.py",
    } <= inputs
