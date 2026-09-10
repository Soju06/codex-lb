"""Small, shared primitives for privacy-safe traffic evidence artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_COPY_CHUNK_BYTES = 1024 * 1024


def read_json(path: str | Path) -> Any:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def file_digest(path: str | Path) -> dict[str, int | str]:
    source = Path(path)
    digest = hashlib.sha256()
    byte_count = 0
    with source.open("rb") as handle:
        while chunk := handle.read(_COPY_CHUNK_BYTES):
            byte_count += len(chunk)
            digest.update(chunk)
    return {"bytes": byte_count, "sha256": digest.hexdigest()}


def file_attestation(label: str, path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        digest = file_digest(source)
    except OSError as exc:
        return {
            "label": label,
            "path": str(source),
            "bytes": None,
            "sha256": None,
            "available": False,
            "error": type(exc).__name__,
        }
    return {
        "label": label,
        "path": str(source),
        **digest,
        "available": True,
        "error": None,
    }


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_text(path: str | Path, text: str, *, mode: int = 0o644) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def atomic_write_json(
    path: str | Path,
    value: Mapping[str, Any],
    *,
    mode: int = 0o644,
) -> None:
    atomic_write_text(
        path,
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        mode=mode,
    )
