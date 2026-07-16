from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _fleet_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "root"
    deploy = root / "deploy"
    scripts = deploy / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "deploy/scripts/fleet.sh", scripts / "fleet.sh")
    (deploy / "docker-compose.intranet.yml").touch()
    (deploy / "docker-compose.sandbox.yml").touch()
    (deploy / ".env").write_text("AF_ENABLE_SANDBOX=0\n", encoding="utf-8")
    (deploy / "fleet.conf").write_text(
        "infra local\nrelease local\napp local scale=2\nlb local\n",
        encoding="utf-8",
    )

    release = root / ".artifactflow/releases/app-v1"
    (release / "deploy").mkdir(parents=True)
    (release / "config").mkdir()
    (release / "deploy/docker-compose.intranet.yml").touch()
    (release / ".af-release").write_text(
        "release_id=app-v1\nkind=app\napp_version=app-v1\nbundle_digest=test\n",
        encoding="utf-8",
    )
    current = root / ".artifactflow/current"
    current.symlink_to(release, target_is_directory=True)
    (deploy / ".fleet-state").write_text(
        "current=app-v1\nprevious=\n", encoding="utf-8"
    )
    return root, scripts / "fleet.sh"


def test_deploy_config_dry_run_does_not_require_app_tar(tmp_path: Path) -> None:
    root, fleet = _fleet_root(tmp_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    version = "config-v2"
    (bundle / f"artifactflow-{version}.manifest.txt").write_text(
        "\n".join(
            [
                f"ArtifactFlow Release {version}",
                "Release kind: config",
                f"Platform:     linux/{arch}",
                "Layout:       config",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (bundle / f"artifactflow-config-{version}.tar.gz").touch()

    result = subprocess.run(
        ["bash", str(fleet), "deploy-config", "--dry-run", str(bundle)],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "app stays app-v1" in result.stdout
    assert "app tar" not in result.stderr.lower()


def test_app_deploy_dry_run_plans_immutable_release_without_writes(
    tmp_path: Path,
) -> None:
    root, fleet = _fleet_root(tmp_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    version = "app-v2"
    (bundle / f"artifactflow-{version}.manifest.txt").write_text(
        "\n".join(
            [
                f"ArtifactFlow Release {version}",
                "Release kind: app",
                f"Platform:     linux/{arch}",
                f"Sandbox image required: artifactflow-sandbox:0123456789abcdef-{arch}",
                "gVisor host runtime: none",
                "Layout:       app + config + deploy",
                "",
            ]
        ),
        encoding="utf-8",
    )
    for unit in ("app", "config", "deploy"):
        (bundle / f"artifactflow-{unit}-{version}.tar.gz").touch()

    result = subprocess.run(
        ["bash", str(fleet), "deploy", "--dry-run", str(bundle)],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "stage immutable release app-v2" in result.stdout
    assert "current remains untouched" in result.stdout
    assert not (root / ".artifactflow/releases/app-v2").exists()


def test_status_returns_nonzero_when_services_and_lb_are_down(tmp_path: Path) -> None:
    root, fleet = _fleet_root(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "docker", "#!/bin/sh\nexit 1\n")
    _write_executable(fake_bin / "curl", "#!/bin/sh\nexit 1\n")
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", str(fleet), "status"],
        cwd=root,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "LB /health/ready NOT green" in result.stderr
