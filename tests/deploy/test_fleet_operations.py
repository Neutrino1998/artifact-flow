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
    shutil.copy2(
        ROOT / "deploy/scripts/config-hotfix.sh", scripts / "config-hotfix.sh"
    )
    shutil.copy2(
        ROOT / "deploy/scripts/verify-bundle.sh", scripts / "verify-bundle.sh"
    )
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
    (release / "config/models").mkdir()
    (release / "config/models/models.yaml").write_text(
        "endpoint: http://old.internal\n", encoding="utf-8"
    )
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


def test_deploy_config_rejects_retained_bundle_after_base_changes(
    tmp_path: Path,
) -> None:
    root, fleet = _fleet_root(tmp_path)
    bundle = tmp_path / "stale-bundle"
    bundle.mkdir()
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    version = "hotfix-from-v1"
    (bundle / f"artifactflow-{version}.manifest.txt").write_text(
        "\n".join(
            [
                f"ArtifactFlow Release {version}",
                "Release kind: config",
                "Expected base release: app-v1",
                f"Platform:     linux/{arch}",
                "Layout:       config",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (bundle / f"artifactflow-config-{version}.tar.gz").touch()

    release = root / ".artifactflow/releases/app-v2"
    (release / "deploy").mkdir(parents=True)
    (release / "config").mkdir()
    (release / "deploy/docker-compose.intranet.yml").touch()
    (release / ".af-release").write_text(
        "release_id=app-v2\nkind=app\napp_version=app-v2\nbundle_digest=test-v2\n",
        encoding="utf-8",
    )
    current = root / ".artifactflow/current"
    current.unlink()
    current.symlink_to(release, target_is_directory=True)
    (root / "deploy/.fleet-state").write_text(
        "current=app-v2\nprevious=app-v1\n", encoding="utf-8"
    )

    result = subprocess.run(
        ["bash", str(fleet), "deploy-config", "--dry-run", str(bundle)],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "expects base app-v1 but active release is app-v2" in result.stderr


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


def test_config_hotfix_checkout_and_apply_dry_run(tmp_path: Path) -> None:
    root, fleet = _fleet_root(tmp_path)
    workspace = tmp_path / "model-hotfix"

    checkout = subprocess.run(
        ["bash", str(fleet), "config", "checkout", str(workspace)],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )
    assert checkout.returncode == 0, checkout.stderr
    model_yaml = workspace / "config/models/models.yaml"
    model_yaml.write_text("endpoint: http://new.internal\n", encoding="utf-8")

    apply = subprocess.run(
        [
            "bash",
            str(fleet),
            "config",
            "apply",
            "--id",
            "hotfix-model-test",
            "--dry-run",
            str(workspace),
        ],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )

    assert apply.returncode == 0, apply.stderr
    assert "deploy config release=hotfix-model-test" in apply.stdout
    assert "from base app-v1" in apply.stdout
    assert "app stays app-v1" in apply.stdout
    assert not (root / ".artifactflow/hotfix-bundles").exists()


def test_config_hotfix_bundle_records_enforced_base(tmp_path: Path) -> None:
    root, fleet = _fleet_root(tmp_path)
    workspace = tmp_path / "manifest-hotfix"
    subprocess.run(
        ["bash", str(fleet), "config", "checkout", str(workspace)],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    )
    (workspace / "config/models/models.yaml").write_text(
        "endpoint: http://new.internal\n", encoding="utf-8"
    )

    # Let bundle generation finish, then fail the delegated deploy so the real
    # bundle remains available for contract inspection.
    _write_executable(fleet, "#!/bin/sh\nexit 1\n")
    bundle_root = tmp_path / "hotfix-bundles"
    env = os.environ.copy()
    env["AF_HOTFIX_BUNDLE_DIR"] = str(bundle_root)
    result = subprocess.run(
        [
            "bash",
            str(root / "deploy/scripts/config-hotfix.sh"),
            "apply",
            "--id",
            "hotfix-manifest-test",
            str(workspace),
        ],
        cwd=root,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    manifest = (
        bundle_root
        / "hotfix-manifest-test/artifactflow-hotfix-manifest-test.manifest.txt"
    ).read_text(encoding="utf-8")
    assert "Expected base release: app-v1" in manifest


def test_config_hotfix_rejects_stale_checkout(tmp_path: Path) -> None:
    root, fleet = _fleet_root(tmp_path)
    workspace = tmp_path / "stale-hotfix"
    subprocess.run(
        ["bash", str(fleet), "config", "checkout", str(workspace)],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    )
    (workspace / "config/models/models.yaml").write_text(
        "endpoint: http://new.internal\n", encoding="utf-8"
    )
    active_config = root / ".artifactflow/current/config/models/models.yaml"
    active_config.write_text("endpoint: http://concurrent.internal\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(fleet), "config", "apply", "--dry-run", str(workspace)],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "active config changed in place" in result.stderr


def test_release_identity_includes_app_archive_and_config_lineage(
    tmp_path: Path,
) -> None:
    _, fleet = _fleet_root(tmp_path)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first_app = first_dir / "artifactflow-app-v2.tar.gz"
    second_app = second_dir / "artifactflow-app-v2.tar.gz"
    first_app.write_bytes(b"first image")
    second_app.write_bytes(b"second image")
    deploy_tar = tmp_path / "artifactflow-deploy-v2.tar.gz"
    config_tar = tmp_path / "artifactflow-config-v2.tar.gz"
    deploy_tar.write_bytes(b"deploy")
    config_tar.write_bytes(b"config")

    def digest(lineage: str, *files: Path) -> str:
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; shift; release_identity_digest "$@"',
                "_",
                str(fleet),
                lineage,
                *(str(file) for file in files),
            ],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    assert digest("app", first_app, deploy_tar, config_tar) != digest(
        "app", second_app, deploy_tar, config_tar
    )
    assert digest("base:app-v1", config_tar) != digest("base:app-v0", config_tar)


def test_multi_host_upstreams_use_advertise_and_all_frontends(
    tmp_path: Path,
) -> None:
    root, fleet = _fleet_root(tmp_path)
    (root / "deploy/fleet.conf").write_text(
        "\n".join(
            [
                "infra local",
                "release local",
                "app local scale=1 advertise=app1.internal",
                "app app2 scale=1 advertise=app2.internal",
                "lb local",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; parse_conf; parse_conf; render_multi_host_upstreams',
            "_",
            str(fleet),
        ],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "app1.internal:8000 app2.internal:8000" in result.stdout
    assert "app1.internal:3000 app2.internal:3000" in result.stdout
    assert "local:8000" not in result.stdout


def test_multi_host_requires_explicit_lb_reachable_app_address(
    tmp_path: Path,
) -> None:
    root, fleet = _fleet_root(tmp_path)
    (root / "deploy/fleet.conf").write_text(
        "infra local\nrelease local\napp local scale=1\napp app2 scale=1\nlb local\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", "-c", 'source "$1"; parse_conf', "_", str(fleet)],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "needs advertise=<LB-reachable-address>" in result.stderr


def test_remote_config_staging_checks_current_base(tmp_path: Path) -> None:
    root, fleet = _fleet_root(tmp_path)
    bundle = tmp_path / "remote-config"
    bundle.mkdir()
    version = "config-v2"
    (bundle / f"artifactflow-config-{version}.tar.gz").write_bytes(b"config")
    manifest = bundle / f"artifactflow-{version}.manifest.txt"
    manifest.touch()

    result = subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(
                [
                    'source "$1"',
                    'BUNDLE="$2"',
                    f'BUNDLE_VER="{version}"',
                    'BUNDLE_MANIFEST="$3"',
                    "DRY=0",
                    'run_on() { printf "%s\\n" "$2"; }',
                    "copy_release_file() { :; }",
                    'prepare_config_host app2 app-v1 app-v1',
                ]
            ),
            "_",
            str(fleet),
            str(bundle),
            str(manifest),
        ],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "active release mismatch: expected app-v1" in result.stdout
    assert "base_release=%s" in result.stdout


def test_fleet_mutations_are_serialized_on_control_host(tmp_path: Path) -> None:
    root, fleet = _fleet_root(tmp_path)
    holder = subprocess.Popen(
        [
            "bash",
            "-c",
            'source "$1"; acquire_mutation_lock holder; echo locked; read -r _',
            "_",
            str(fleet),
        ],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        contender = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; acquire_mutation_lock contender',
                "_",
                str(fleet),
            ],
            cwd=root,
            check=False,
            text=True,
            capture_output=True,
        )
        assert contender.returncode != 0
        assert "another Fleet mutation is running" in contender.stderr
    finally:
        if holder.poll() is None:
            holder.communicate(input="release\n", timeout=5)

    assert not (root / ".artifactflow/fleet-mutation.lock").exists()


def test_bootstrap_plan_and_apply_do_not_share_parser_state(tmp_path: Path) -> None:
    root, fleet = _fleet_root(tmp_path)
    bundle = tmp_path / "bootstrap-bundle"
    bundle.mkdir()
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    version = "bootstrap-v2"
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
        ["bash", str(fleet), "bootstrap", str(bundle)],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )

    # The intentionally incomplete bundle reaches real checksum verification.
    # Previously it stopped earlier on the second parse with duplicate rows.
    assert result.returncode != 0
    assert "duplicate row" not in result.stderr
    assert "checksum verification failed" in result.stderr
    assert not (root / ".artifactflow/fleet-mutation.lock").exists()


def test_backend_runtime_copy_and_resume_fingerprint_are_aligned() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    release_script = (ROOT / "scripts/release.sh").read_text(encoding="utf-8")

    assert "scripts/reconcile_config.py ./scripts/" in dockerfile
    assert "run_server.py alembic.ini" in dockerfile
    fingerprint_line = next(
        line
        for line in release_script.splitlines()
        if "APP_FINGERPRINT=" in line and "paths_fingerprint" in line
    )
    assert "scripts" in fingerprint_line
    assert "alembic.ini" in fingerprint_line
