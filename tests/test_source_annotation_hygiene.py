"""Best-effort guard against high-confidence implementation-history labels."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = (
    ROOT / "src",
    ROOT / "frontend" / "src",
    ROOT / "sandbox",
    ROOT / "scripts",
    ROOT / "deploy",
    ROOT / "config",
)
ROOT_SOURCE_FILES = (
    ROOT / "Dockerfile",
    ROOT / "requirements.txt",
    ROOT / "docker-compose.dev.yml",
)
SOURCE_SUFFIXES = {
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".sh",
    ".bash",
    ".yml",
    ".yaml",
    ".toml",
    ".md",
    ".txt",
    ".sql",
}
SOURCE_FILENAMES = {"Dockerfile", "Caddyfile"}
RUNTIME_MARKDOWN_ROOT = ROOT / "config"
EXCLUDED_FILES = {
    ROOT / "frontend" / "src" / "types" / "api.d.ts",
    ROOT / "frontend" / "src" / "types" / "openapi.json",
}
EXCLUDED_DIR_NAMES = {"__pycache__", "node_modules", "dist"}

FORBIDDEN_PATTERNS = (
    (
        "archived-doc reference",
        re.compile(r"docs[/\\]_archive(?:[/\\]|$)", re.IGNORECASE),
    ),
    ("PR label", re.compile(r"\bPR\s*#?\s*\d+[a-z]?\b", re.IGNORECASE)),
    (
        "decision label",
        re.compile(r"\bdecision\s*#?\s*\d+[a-z]?\b", re.IGNORECASE),
    ),
    ("plan section", re.compile(r"\bplan\s*§\s*\S+", re.IGNORECASE)),
    (
        "numbered review",
        re.compile(
            r"\breview(?:er)?\s*(?:#|r)\s*\d+\b|"
            r"(?:\d+|[一二两三四五六七八九十]+)\s*轮\s*review\b",
            re.IGNORECASE,
        ),
    ),
)


def _is_test_file(path: Path) -> bool:
    return (
        "__tests__" in path.parts
        or ".test." in path.name
        or ".spec." in path.name
        or path.name.startswith("test_")
    )


def _iter_source_files():
    for source_dir in SOURCE_DIRS:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or path in EXCLUDED_FILES:
                continue
            if EXCLUDED_DIR_NAMES.intersection(path.parts) or _is_test_file(path):
                continue
            if (
                path.suffix.lower() == ".md"
                and RUNTIME_MARKDOWN_ROOT not in path.parents
            ):
                continue
            if path.suffix.lower() in SOURCE_SUFFIXES or path.name in SOURCE_FILENAMES:
                yield path
    yield from (path for path in ROOT_SOURCE_FILES if path.is_file())


def _matches_forbidden_pattern(text: str) -> bool:
    return any(pattern.search(text) for _, pattern in FORBIDDEN_PATTERNS)


def test_annotation_guard_patterns_stay_narrow():
    forbidden_examples = (
        "PR5a",
        "decision 11",
        "review r4",
        "reviewer #2",
        "两轮 review",
        "plan §B",
        "docs/_archive/design.md",
    )
    allowed_examples = (
        "Phase 0",
        "OPEN 阶段",
        "TTFT 阶段",
        "--author Reviewer",
        "peer-reviewed source",
        "2026-05-14",
    )

    assert all(_matches_forbidden_pattern(text) for text in forbidden_examples)
    assert not any(_matches_forbidden_pattern(text) for text in allowed_examples)


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
