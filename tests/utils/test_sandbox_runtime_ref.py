from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/sandbox_runtime_ref.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sandbox_runtime_ref", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_runtime_slug_is_deterministic_and_content_sensitive(tmp_path: Path) -> None:
    module = _load_module()
    for relative in module.RUNTIME_INPUTS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture:{relative}\n", encoding="utf-8")

    first = module.runtime_slug(tmp_path)
    assert first == module.runtime_slug(tmp_path)
    (tmp_path / module.RUNTIME_INPUTS[0]).write_text("changed\n", encoding="utf-8")
    assert module.runtime_slug(tmp_path) != first


def test_runtime_ref_cli_is_recipe_addressed() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--arch", "amd64"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    assert re.fullmatch(
        r"artifactflow-sandbox:[0-9a-f]{16}-amd64", result.stdout.strip()
    )


def test_release_always_carries_exact_sandbox_image() -> None:
    release = (ROOT / "scripts/release.sh").read_text(encoding="utf-8")
    compose = (ROOT / "deploy/compose.sandbox.yml").read_text(encoding="utf-8")
    assert 'SANDBOX_IMAGE="$(<"$SANDBOX_REF_FILE")"' in release
    assert "artifactflow-sandbox:sha256-" in release
    assert "--artifact \"sandbox=$SANDBOX_ARCHIVE\"" in release
    assert "--image \"$SANDBOX_IMAGE\"" in release
    assert "AF_ENABLE_SANDBOX" not in release
    assert "afctl always" in compose


def test_runtime_inputs_cover_files_copied_into_image() -> None:
    module = _load_module()
    assert {
        "sandbox/Dockerfile",
        "sandbox/requirements.txt",
        "sandbox/office_cli.py",
        "sandbox/text_edit.py",
        "src/utils/text_match.py",
    } <= set(module.RUNTIME_INPUTS)
