"""Static checks for the project's layer boundaries.

These tests intentionally inspect imports and transaction calls instead of
runtime behavior.  A boundary violation should fail close to the change that
introduced it, before it grows into another cross-layer dependency.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parents[1] / "src"


def _python_files(path: Path) -> list[Path]:
    return sorted(path.rglob("*.py"))


def _module_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))
    return imports


def _assert_no_import_prefix(path: Path, prefixes: tuple[str, ...]) -> None:
    violations = [
        f"{path.relative_to(SRC)}:{line} imports {module}"
        for line, module in _module_imports(path)
        if any(
            module == prefix or module.startswith(f"{prefix}.")
            for prefix in prefixes
        )
    ]
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("path", _python_files(SRC / "api" / "routers"))
def test_routers_do_not_import_persistence_or_other_routers(path: Path) -> None:
    _assert_no_import_prefix(path, ("db", "repositories", "api.routers"))


@pytest.mark.parametrize("path", _python_files(SRC / "core"))
def test_core_does_not_depend_on_api(path: Path) -> None:
    _assert_no_import_prefix(path, ("api",))


@pytest.mark.parametrize("path", _python_files(SRC / "core" / "management"))
def test_managers_only_use_sqlalchemy_for_session_typing(path: Path) -> None:
    sqlalchemy_imports = [
        (line, module)
        for line, module in _module_imports(path)
        if module == "sqlalchemy" or module.startswith("sqlalchemy.")
    ]
    violations = [
        f"{path.relative_to(SRC)}:{line} imports {module}"
        for line, module in sqlalchemy_imports
        if module != "sqlalchemy.ext.asyncio"
    ]
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("path", _python_files(SRC / "core" / "management"))
def test_managers_do_not_control_session_transactions(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden_methods = {
        "add",
        "commit",
        "delete",
        "execute",
        "expire",
        "flush",
        "rollback",
    }
    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in forbidden_methods:
            continue

        owner = node.func.value
        owner_parts: list[str] = []
        while isinstance(owner, ast.Attribute):
            owner_parts.append(owner.attr)
            owner = owner.value
        if isinstance(owner, ast.Name):
            owner_parts.append(owner.id)

        if any("session" in part.lower() for part in owner_parts):
            violations.append(
                f"{path.relative_to(SRC)}:{node.lineno} calls session.{node.func.attr}()"
            )

    assert not violations, "\n".join(violations)
