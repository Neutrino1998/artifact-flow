from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_shell_entrypoints_are_syntax_valid() -> None:
    scripts = [
        ROOT / "scripts/release.sh",
        ROOT / "deploy/scripts/fleet.sh",
        ROOT / "deploy/scripts/maintenance.sh",
        ROOT / "deploy/scripts/autoheal.sh",
    ]
    for path in scripts:
        subprocess.run(["bash", "-n", str(path)], check=True)


def test_fleet_is_only_a_thin_compatibility_bridge() -> None:
    fleet = (ROOT / "deploy/scripts/fleet.sh").read_text(encoding="utf-8")
    assert len(fleet.splitlines()) < 80
    assert ".fleet-state" not in fleet
    assert ".artifactflow/current" not in fleet
    assert "ssh " not in fleet
    assert "scp " not in fleet
    assert '[[ $# -eq 1 && "$1" == "--dry-run" ]]' in fleet


def test_release_script_is_build_only_and_emits_strict_manifest() -> None:
    release = (ROOT / "scripts/release.sh").read_text(encoding="utf-8")
    assert "release manifest" in release
    assert "--config-only" not in release
    assert "--with-gvisor" not in release
    assert "--with-analyst-tools" not in release
    assert "fleet.sh" not in release
    assert "AF_ENABLE_SANDBOX" not in release
    assert "afctl prepare" not in release
    assert 'DEPLOY_STAGE/deploy/bin' not in release
    assert "artifactflow-release-${VERSION}-${ARCH}.tar" in release


def test_production_compose_has_no_build_latest_or_sandbox_fallback() -> None:
    base = (ROOT / "deploy/compose.base.yml").read_text(encoding="utf-8")
    sandbox = (ROOT / "deploy/compose.sandbox.yml").read_text(encoding="utf-8")
    assert not re.search(r"^\s*build:", base, re.MULTILINE)
    assert ":latest" not in base
    assert "AF_VERSION:?afctl must set AF_VERSION" in base
    assert "AF_CADDY_IMAGE:?afctl must set AF_CADDY_IMAGE" in base
    assert "AF_POSTGRES_IMAGE:?afctl must set AF_POSTGRES_IMAGE" in base
    assert "AF_REDIS_IMAGE:?afctl must set AF_REDIS_IMAGE" in base
    assert "ARTIFACTFLOW_SANDBOX_RUNTIME:?afctl must set sandbox runtime" in sandbox
    assert "ARTIFACTFLOW_SANDBOX_RUNTIME:-runsc" not in sandbox
    assert "/var/run/docker.sock:/var/run/docker.sock" in sandbox


def test_public_and_intranet_share_one_compose_base() -> None:
    assert not (ROOT / "docker-compose.prod.yml").exists()
    assert (ROOT / "deploy/compose.base.yml").is_file()
    acme = (ROOT / "deploy/compose.tls-acme.yml").read_text(encoding="utf-8")
    assert "Caddyfile.acme" in acme
    assert "AF_DOMAIN:?" in acme


def test_ansible_renders_the_snippets_caddy_imports_and_leaves_state_order_to_afctl() -> None:
    playbook = (ROOT / "deploy/ansible/apply.yml").read_text(encoding="utf-8")
    caddy = (ROOT / "deploy/caddy/common.caddy").read_text(encoding="utf-8")
    assert "(backend_upstream_targets)" in playbook
    assert "(frontend_upstream_targets)" in playbook
    assert "import backend_upstream_targets" in caddy
    assert "import frontend_upstream_targets" in caddy
    assert "Disable maintenance after health succeeds" not in playbook
    assert "serial: 1" in playbook


def test_maintenance_assets_and_target_local_state_are_separate_mounts() -> None:
    base = (ROOT / "deploy/compose.base.yml").read_text(encoding="utf-8")
    assert "./maintenance:/etc/caddy/maintenance:ro" in base
    assert "${AF_RUNTIME_DEPLOY_DIR:-.}/maintenance:/etc/caddy/maintenance-state:ro" in base


def test_manifest_schema_example_is_strict_json_shape() -> None:
    # The Go contract is tested deeply in internal/afctl. This Python-side
    # guard keeps packaging/docs from drifting away from its fixed filename.
    source = (ROOT / "internal/afctl/manifest.go").read_text(encoding="utf-8")
    assert 'filepath.Join(bundle, "manifest.json")' in source
    assert "DisallowUnknownFields" in (
        ROOT / "internal/afctl/json.go"
    ).read_text(encoding="utf-8")
    schema = {
        "schema": 1,
        "release_id": "example",
        "kind": "config",
        "platform": "linux/amd64",
        "created_at": "2026-01-01T00:00:00Z",
        "source": "test",
        "expected_base_release": "base",
        "artifacts": [],
    }
    assert json.loads(json.dumps(schema))["schema"] == 1
