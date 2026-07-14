import importlib.util
import os
import platform
import re
import shutil
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
    assert "\\n+      AF_SANDBOX_" not in release
    assert "SANDBOX_UP_PREFIX" not in release
    assert "AF_ENABLE_SANDBOX=1 deploy/scripts/fleet.sh deploy" not in release
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
    gvisor_name = f"sandbox-gvisor-release-20260706.0-{gvisor_arch}.tar.gz"
    manifest = tmp_path / f"artifactflow-{version}.manifest.txt"
    manifest.write_text(
        "\n".join([
            f"ArtifactFlow Release {version}",
            f"Platform:     linux/{image_arch}",
            f"Sandbox image required: {image_ref}",
            f"gVisor host runtime: {gvisor_name if include_gvisor else 'skipped — target must already have runsc registered.'}",
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
        names.append(gvisor_name)
    stale_gvisor = f"sandbox-gvisor-release-20260504.0-{gvisor_arch}.tar.gz"
    names.append(stale_gvisor)
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
        assert f"AF_GVISOR_PACKAGE={tmp_path / gvisor_name}" in result.stdout
        assert stale_gvisor not in result.stdout
    else:
        assert "would require existing runsc registration" in result.stdout
        assert "AF_GVISOR_PACKAGE= deploy/scripts/prepare-host.sh sandbox" in result.stdout
        assert stale_gvisor not in result.stdout


def test_prepare_sandbox_rejects_missing_manifest_declared_gvisor(tmp_path):
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        image_arch = "amd64"
        gvisor_arch = "x86_64"
    elif machine in {"arm64", "aarch64"}:
        image_arch = "arm64"
        gvisor_arch = "aarch64"
    else:
        raise AssertionError(f"unsupported test architecture: {machine}")

    version = "missing-gvisor-test"
    gvisor_name = f"sandbox-gvisor-release-20260706.0-{gvisor_arch}.tar.gz"
    (tmp_path / f"artifactflow-{version}.manifest.txt").write_text(
        "\n".join([
            f"ArtifactFlow Release {version}",
            f"Platform:     linux/{image_arch}",
            f"Sandbox image required: artifactflow-sandbox:0123456789abcdef-{image_arch}",
            f"gVisor host runtime: {gvisor_name}",
            "",
        ]),
        encoding="utf-8",
    )
    (tmp_path / f"artifactflow-app-{version}.tar.gz").touch()

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
    assert "manifest-declared gVisor package not found" in result.stderr


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
            "gVisor host runtime: none",
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


def _fleet_dry_run_from_env_file(tmp_path, sandbox_policy):
    root = tmp_path / f"root-{sandbox_policy}"
    scripts = root / "deploy" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "deploy" / "scripts" / "fleet.sh", scripts / "fleet.sh")
    (root / "deploy" / "docker-compose.intranet.yml").touch()
    (root / "deploy" / "docker-compose.sandbox.yml").touch()
    (root / "deploy" / ".env").write_text(
        f"AF_ENABLE_SANDBOX={sandbox_policy}\n",
        encoding="utf-8",
    )
    (root / "deploy" / "fleet.conf").write_text(
        "infra local\nrelease local\napp local scale=1\nlb local\n",
        encoding="utf-8",
    )

    machine = platform.machine().lower()
    image_arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    version = f"env-policy-{sandbox_policy}"
    bundle = tmp_path / f"bundle-{sandbox_policy}"
    bundle.mkdir()
    (bundle / f"artifactflow-{version}.manifest.txt").write_text(
        "\n".join([
            f"ArtifactFlow Release {version}",
            f"Platform:     linux/{image_arch}",
            f"Sandbox image required: artifactflow-sandbox:0123456789abcdef-{image_arch}",
            "gVisor host runtime: none",
            "",
        ]),
        encoding="utf-8",
    )
    for kind in ("app", "config", "deploy"):
        (bundle / f"artifactflow-{kind}-{version}.tar.gz").touch()

    env = os.environ.copy()
    env.pop("AF_ENABLE_SANDBOX", None)
    return subprocess.run(
        ["bash", str(scripts / "fleet.sh"), "deploy", "--dry-run", str(bundle)],
        cwd=root,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )


@pytest.mark.parametrize("sandbox_policy", ["0", "1"])
def test_fleet_reads_persistent_sandbox_policy_from_deploy_env(tmp_path, sandbox_policy):
    result = _fleet_dry_run_from_env_file(tmp_path, sandbox_policy)
    assert result.returncode == 0, result.stderr
    assert f"sandbox={sandbox_policy}" in result.stdout
    if sandbox_policy == "1":
        assert "docker-compose.sandbox.yml" in result.stdout
    else:
        assert "docker-compose.sandbox.yml" not in result.stdout


def test_fleet_rejects_missing_sandbox_policy_before_deploy(tmp_path):
    result = _fleet_dry_run_from_env_file(tmp_path, "")
    assert result.returncode != 0
    assert "AF_ENABLE_SANDBOX is not configured" in result.stderr
