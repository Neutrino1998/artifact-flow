import builtins
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def office_cli():
    path = ROOT / "sandbox" / "office_cli.py"
    spec = importlib.util.spec_from_file_location("artifactflow_office_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_pages_is_strict_and_bounded(office_cli, monkeypatch):
    assert office_cli._parse_pages("1-3,5", 5) == [0, 1, 2, 4]
    with pytest.raises(office_cli.OfficeError, match="within 1-5"):
        office_cli._parse_pages("6", 5)
    monkeypatch.setattr(office_cli, "MAX_RENDER_PAGES", 2)
    with pytest.raises(office_cli.OfficeError, match="select at most 2"):
        office_cli._parse_pages(None, 3)
    with pytest.raises(office_cli.OfficeError, match="render at most 2"):
        office_cli._parse_pages("1-3", 3)


def test_parse_pages_rejects_huge_range_before_materializing(office_cli, monkeypatch):
    real_range = builtins.range

    def guarded_range(*args):
        candidate = real_range(*args)
        assert len(candidate) <= office_cli.MAX_RENDER_PAGES, "range materialized before limit check"
        return candidate

    monkeypatch.setattr(office_cli, "range", guarded_range, raising=False)
    with pytest.raises(office_cli.OfficeError, match="within 1-10"):
        office_cli._parse_pages("1-999999999", 10)
    with pytest.raises(office_cli.OfficeError, match="render at most 100"):
        office_cli._parse_pages("1-999999999", 999999999)


def test_html_is_not_a_single_file_conversion(office_cli):
    assert ".html" not in office_cli._CONVERT_FILTERS
    with pytest.raises(office_cli.OfficeError, match="unsupported output extension"):
        office_cli._conversion_spec(".html")


def test_convert_writes_the_explicit_target(office_cli, monkeypatch, tmp_path):
    source = tmp_path / "input name.docx"
    source.write_bytes(b"fixture")
    target = tmp_path / "renamed output.pdf"

    monkeypatch.setattr(office_cli, "_soffice", lambda: "/fake/soffice")

    def fake_run(command, *, env):
        outdir = Path(command[command.index("--outdir") + 1])
        suffix = "." + command[command.index("--convert-to") + 1].split(":", 1)[0]
        (outdir / f"{source.stem}{suffix}").write_bytes(b"converted")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(office_cli, "_run_process", fake_run)
    payload = office_cli.convert(source, target)

    assert payload["ok"] is True
    assert payload["output"] == str(target)
    assert target.read_bytes() == b"converted"


def test_convert_fails_when_libreoffice_creates_no_file(office_cli, monkeypatch, tmp_path):
    source = tmp_path / "input.docx"
    source.write_bytes(b"fixture")
    target = tmp_path / "output.pdf"

    monkeypatch.setattr(office_cli, "_soffice", lambda: "/fake/soffice")
    monkeypatch.setattr(
        office_cli,
        "_run_process",
        lambda command, env: subprocess.CompletedProcess(command, 0, "no output", ""),
    )

    with pytest.raises(office_cli.OfficeError, match="created 0 .pdf file"):
        office_cli.convert(source, target)
    assert not target.exists()
