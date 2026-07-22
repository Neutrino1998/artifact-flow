#!/usr/bin/env python3
"""Derive the sandbox build-cache recipe reference from runtime inputs.

Production releases retag the built image by its actual Docker image ID.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


RUNTIME_INPUTS = (
    "sandbox/Dockerfile",
    "sandbox/requirements.txt",
    "sandbox/office_cli.py",
    "sandbox/text_edit.py",
    "sandbox/stub-pkg/pyproject.toml",
    "sandbox/stub-pkg/af_sandbox_stub/__init__.py",
    "src/utils/text_match.py",
)
SLUG_HEX_CHARS = 16


def runtime_digest(root: Path) -> str:
    digest = hashlib.sha256()
    digest.update(b"artifactflow-sandbox-runtime-inputs-v1\0")
    for relative in RUNTIME_INPUTS:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"sandbox runtime input is missing: {relative}")
        name = relative.encode("utf-8")
        content = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def runtime_slug(root: Path) -> str:
    return runtime_digest(root)[:SLUG_HEX_CHARS]


def runtime_ref(root: Path, arch: str) -> str:
    if arch not in {"amd64", "arm64"}:
        raise ValueError(f"unsupported sandbox architecture: {arch}")
    return f"artifactflow-sandbox:{runtime_slug(root)}-{arch}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--arch", choices=("amd64", "arm64"))
    parser.add_argument("--full", action="store_true", help="print the full digest")
    args = parser.parse_args()

    root = args.root.resolve()
    if args.full:
        print(runtime_digest(root))
    elif args.arch:
        print(runtime_ref(root, args.arch))
    else:
        print(runtime_slug(root))


if __name__ == "__main__":
    main()
