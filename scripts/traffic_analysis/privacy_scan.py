"""Scan retained traffic evidence for credential-shaped values."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

try:
    from scripts.traffic_analysis.artifacts import atomic_write_json
except ModuleNotFoundError:  # Allow direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.traffic_analysis.artifacts import atomic_write_json

_CHUNK_BYTES = 1024 * 1024
_OVERLAP_BYTES = 512
_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    (
        "bearer_token",
        re.compile(rb"(?i)\bbearer[ \t]+(?!\[(?:redacted|sha256)[^\]]*\])[^\s\"']{8,}"),
    ),
    ("secret_key", re.compile(rb"\bsk-[A-Za-z0-9_-]{12,}")),
    ("jwt", re.compile(rb"\beyJ[A-Za-z0-9_-]{12,}\.eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}")),
    (
        "oauth_token_field",
        re.compile(
            rb'(?i)"(?:access_token|refresh_token|id_token)"\s*:\s*"'
            rb'(?!\[(?:redacted|sha256)[^\]]*\])[^"\r\n]{12,}'
        ),
    ),
)


def _file_findings(path: Path) -> list[str]:
    findings: set[str] = set()
    tail = b""
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            sample = tail + chunk
            for label, pattern in _PATTERNS:
                if pattern.search(sample):
                    findings.add(label)
            tail = sample[-_OVERLAP_BYTES:]
    return sorted(findings)


def scan_tree(root: str | Path) -> dict[str, Any]:
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ValueError(f"privacy scan root must be an existing directory: {root_path}")
    findings: list[dict[str, Any]] = []
    files_scanned = 0
    bytes_scanned = 0
    for current, directory_names, file_names in os.walk(root_path, followlinks=False):
        current_path = Path(current)
        retained_directories: list[str] = []
        for name in sorted(directory_names):
            candidate = current_path / name
            if candidate.is_symlink():
                findings.append({"path": str(candidate.relative_to(root_path)), "kinds": ["symlink"]})
            else:
                retained_directories.append(name)
        directory_names[:] = retained_directories
        for name in sorted(file_names):
            candidate = current_path / name
            relative = str(candidate.relative_to(root_path))
            if candidate.is_symlink():
                findings.append({"path": relative, "kinds": ["symlink"]})
                continue
            try:
                size = candidate.stat().st_size
                kinds = _file_findings(candidate)
            except OSError as exc:
                findings.append({"path": relative, "kinds": [f"read_error:{type(exc).__name__}"]})
                continue
            files_scanned += 1
            bytes_scanned += size
            if kinds:
                findings.append({"path": relative, "kinds": kinds})
    return {
        "schema_version": 1,
        "root": str(root_path),
        "passed": not findings,
        "files_scanned": files_scanned,
        "bytes_scanned": bytes_scanned,
        "findings": findings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = scan_tree(args.root)
    atomic_write_json(args.output, result)
    print(f"Privacy scan: {'PASS' if result['passed'] else 'FAIL'}; report: {args.output}")
    return 2 if args.strict and not result["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
