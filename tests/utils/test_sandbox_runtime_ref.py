import importlib.util
import platform
import re
import subprocess
import sys
from pathlib import Path

import pytest


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
    assert "--with-gvisor" in release
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


@pytest.mark.parametrize("include_gvisor", [False, True])
def test_prepare_sandbox_dry_run_preserves_full_image_ref(tmp_path, include_gvisor):
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        image_arch = "amd64"
        gvisor_arch = "x86_64"
    elif machine in {"arm64", "aarch64"}:
        image_arch = "arm64"
        gvisor_arch = "aarch64"
    else:
        raise AssertionError(f"unsupported test architecture: {machine}")

    version = "manifest-parser-test"
    image_ref = f"artifactflow-sandbox:0123456789abcdef-{image_arch}"
    manifest = tmp_path / f"artifactflow-{version}.manifest.txt"
    manifest.write_text(
        "\n".join([
            f"ArtifactFlow Release {version}",
            f"Platform:     linux/{image_arch}",
            f"Sandbox image required: {image_ref}",
            "",
        ]),
        encoding="utf-8",
    )
    names = [
        f"artifactflow-app-{version}.tar.gz",
        f"artifactflow-sandbox-{version}-{image_arch}.tar.gz",
        f"artifactflow-sandbox-verify-{version}.tar.gz",
    ]
    if include_gvisor:
        names.append(f"sandbox-gvisor-release-20260706.0-{gvisor_arch}.tar.gz")
    for name in names:
        (tmp_path / name).touch()

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "deploy" / "scripts" / "fleet.sh"),
            "prepare-sandbox",
            "--dry-run",
            str(tmp_path),
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    assert f"AF_SANDBOX_IMAGE_REF={image_ref}" in result.stdout
    if include_gvisor:
        assert "would install/update runsc" in result.stdout
    else:
        assert "would require existing runsc registration" in result.stdout
        assert "AF_GVISOR_PACKAGE= deploy/scripts/prepare-host.sh sandbox" in result.stdout


def test_prepare_sandbox_rejects_unpaired_image_and_verify(tmp_path):
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        image_arch = "amd64"
    elif machine in {"arm64", "aarch64"}:
        image_arch = "arm64"
    else:
        raise AssertionError(f"unsupported test architecture: {machine}")

    version = "unpaired-sandbox-test"
    image_ref = f"artifactflow-sandbox:0123456789abcdef-{image_arch}"
    (tmp_path / f"artifactflow-{version}.manifest.txt").write_text(
        "\n".join([
            f"ArtifactFlow Release {version}",
            f"Platform:     linux/{image_arch}",
            f"Sandbox image required: {image_ref}",
            "",
        ]),
        encoding="utf-8",
    )
    (tmp_path / f"artifactflow-app-{version}.tar.gz").touch()
    (tmp_path / f"artifactflow-sandbox-{version}-{image_arch}.tar.gz").touch()

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "deploy" / "scripts" / "fleet.sh"),
            "prepare-sandbox",
            "--dry-run",
            str(tmp_path),
        ],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "sandbox image + verify tars must be paired" in result.stderr
