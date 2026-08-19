"""Best-effort guard against high-confidence implementation-history labels."""

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_FILES = {
    "frontend/src/types/api.d.ts",
    "frontend/src/types/openapi.json",
}
EXCLUDED_TOP_LEVEL_DIRS = {"docs", "site", "tests"}
EXCLUDED_DIR_NAMES = {"__tests__", "test-utils"}
EXCLUDED_LOCKFILES = {
    "frontend/package-lock.json",
    "requirements.lock",
}
EXCLUDED_ASSET_SUFFIXES = {".svg"}

FORBIDDEN_PATTERNS = (
    (
        "archived-doc reference",
        re.compile(r"docs[/\\]_archive(?:[/\\]|$)", re.IGNORECASE),
    ),
    (
        "PR label",
        re.compile(
            r"(?i:\bPR\s*#?\s*\d+[a-z]?\b)|"
            r"\bPR-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b|"
            r"(?i:\bpr-[a-z0-9]+(?:-[a-z0-9]+)+\b)",
        ),
    ),
    (
        "decision label",
        re.compile(
            r"\bdecision\s*#?\s*\d+[a-z]?\b|决策\s*#?\s*\d+",
            re.IGNORECASE,
        ),
    ),
    ("finding label", re.compile(r"\bFinding\s*#?\s*\d+[a-z]?\b", re.IGNORECASE)),
    ("plan section", re.compile(r"\bplan\s*§\s*\S+", re.IGNORECASE)),
    (
        "numbered review",
        re.compile(
            r"\breview(?:er)?\s*(?:#|r)\s*\d+\b|"
            r"\breview(?:er)?\s+(?:P[0-3]|N\d+)\b|"
            r"\breview(?:er)?\s+round\s+\d+\b|"
            r"\bP[0-3]\b[^\n]{0,40}\breviewer(?:['’]s)?\s+findings?\b|"
            r"(?:\d+|[一二两三四五六七八九十]+)\s*轮\s*review\b",
            re.IGNORECASE,
        ),
    ),
    (
        "roadmap phase",
        re.compile(
            r"(?i:\b(?:phase|stage)\s+[A-G](?:-\d+)?\b)|"
            r"(?<![A-Za-z])[A-G]-phase\b|"
            r"(?<![A-Za-z])[A-G]-\d+\b|"
            r"(?<![A-Za-z])[A-G][′']?\s*阶段",
        ),
    ),
)


def _is_test_file(relative_path: Path) -> bool:
    return (
        bool(EXCLUDED_DIR_NAMES.intersection(relative_path.parts))
        or ".test." in relative_path.name
        or ".spec." in relative_path.name
        or relative_path.name.startswith("test_")
        or relative_path.name.endswith("_test.go")
    )


def _is_excluded(relative_path: Path) -> bool:
    relative_name = relative_path.as_posix()
    if relative_name in EXCLUDED_FILES or relative_name in EXCLUDED_LOCKFILES:
        return True
    if relative_path.suffix.lower() in EXCLUDED_ASSET_SUFFIXES:
        return True
    if relative_path.parts[0] in EXCLUDED_TOP_LEVEL_DIRS:
        return True
    if _is_test_file(relative_path):
        return True
    # Ordinary Markdown is documentation. Markdown under config/ is runtime
    # agent/tool/skill input and remains in scope.
    if relative_path.suffix.lower() == ".md" and relative_path.parts[0] != "config":
        return True
    return False


def _iter_source_files():
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode("utf-8")
    for relative_name in tracked.split("\0"):
        if not relative_name:
            continue
        relative_path = Path(relative_name)
        if _is_excluded(relative_path):
            continue
        path = ROOT / relative_path
        if not path.is_file():
            continue
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        yield path


def _matches_forbidden_pattern(text: str) -> bool:
    return any(pattern.search(text) for _, pattern in FORBIDDEN_PATTERNS)


def test_annotation_guard_patterns_stay_narrow():
    forbidden_examples = (
        "PR5a",
        "PR-B",
        "PR-C",
        "PR-obs-lite",
        "PR-tz-unify",
        "pr-obs-lite",
        "decision 11",
        "决策 10",
        "Finding 1",
        "review r4",
        "reviewer #2",
        "reviewer P2",
        "reviewer round 4",
        "reviewer N4",
        "P2 in the reviewer's findings",
        "两轮 review",
        "plan §B",
        "docs/_archive/design.md",
        "B-5",
        "C 阶段",
        "Phase C",
        "phase c",
        "Phase G-1",
        "B-phase",
    )
    allowed_examples = (
        "Phase 0",
        "OPEN 阶段",
        "TTFT 阶段",
        "--author Reviewer",
        "peer-reviewed source",
        "2026-05-14",
        "H-264",
        "pr-4",
        "pr-px",
        "P2 severity",
    )

    assert all(_matches_forbidden_pattern(text) for text in forbidden_examples)
    assert not any(_matches_forbidden_pattern(text) for text in allowed_examples)


def test_source_enumeration_covers_production_entrypoints_only():
    source_files = {path.relative_to(ROOT).as_posix() for path in _iter_source_files()}

    assert {
        "cmd/afctl/main.go",
        "internal/afctl/controller.go",
        "frontend/Dockerfile",
        "docker-compose.yml",
        "run_server.py",
        "setup.py",
        "deploy/caddy/common.caddy",
    } <= source_files
    assert {
        "internal/afctl/afctl_test.go",
        "frontend/src/app/login/page.test.tsx",
        "frontend/src/types/api.d.ts",
        "frontend/src/types/openapi.json",
        "frontend/package-lock.json",
        "requirements.lock",
        "docs/index.md",
        "frontend/public/cat-sleep-dark.svg",
    }.isdisjoint(source_files)


def test_production_source_has_no_historical_implementation_labels():
    violations = []
    for path in _iter_source_files():
        relative_path = path.relative_to(ROOT)
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for label, pattern in FORBIDDEN_PATTERNS:
                if pattern.search(line):
                    violations.append(
                        f"{relative_path}:{line_number}: {label}: {line.strip()[:200]}"
                    )

    assert not violations, (
        "Production source contains implementation-history labels. Preserve the "
        "current reason or contract without PR/reviewer/plan numbering:\n"
        + "\n".join(violations)
    )
