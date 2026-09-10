"""Bounded local metadata discovery for preview and targeted repair."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.codex_sessions_retag import _connect_sqlite

SUPPORTED_PROVIDERS = frozenset({"openai", "codex-lb"})
HEADER_LIMIT = 1024 * 1024
Progress = Callable[[str, int], None]


@dataclass(frozen=True)
class JsonlMetadata:
    path: Path
    session_id: str
    provider: str | None
    header: bytes
    identity: tuple[int, int, int, int]


@dataclass(frozen=True)
class StateMetadata:
    identity: tuple[int, int]
    path: Path
    session_id: str
    provider: str | None


@dataclass(frozen=True)
class MetadataPlan:
    jsonl: tuple[JsonlMetadata, ...]
    rows: tuple[StateMetadata, ...]

    def providers(self) -> dict[str, set[str | None]]:
        result: dict[str, set[str | None]] = {}
        for item in (*self.jsonl, *self.rows):
            result.setdefault(item.session_id, set()).add(item.provider)
        return result

    def select(self, session_ids: Sequence[str]) -> MetadataPlan:
        missing = set(session_ids) - self.providers().keys()
        if missing:
            raise ValueError(f"Session IDs not found: {', '.join(sorted(missing))}")
        return MetadataPlan(
            tuple(item for item in self.jsonl if item.session_id in session_ids),
            tuple(item for item in self.rows if item.session_id in session_ids),
        )


def file_identity(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def validate_provider(provider: str) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError("Provider must be openai or codex-lb")


def metadata_payload(header: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON metadata key: {key}")
            result[key] = value
        return result

    record = json.loads(header, object_pairs_hook=unique_object)
    if not isinstance(record, dict):
        raise ValueError("Expected a JSONL session metadata object")
    payload = record.get("payload") if record.get("type") == "session_meta" else record
    if not isinstance(payload, dict):
        raise ValueError("Expected a session metadata payload")
    return record, payload


def read_metadata(path: Path) -> JsonlMetadata | None:
    identity = file_identity(path)
    with path.open("rb") as handle:
        header = handle.readline(HEADER_LIMIT)
        if len(header) == HEADER_LIMIT and not header.endswith(b"\n"):
            raise ValueError(f"Metadata header exceeds 1 MiB: {path}")
    _, payload = metadata_payload(header)
    session_id, provider = payload.get("id"), payload.get("model_provider")
    if not isinstance(session_id, str) or not session_id:
        return None
    if file_identity(path) != identity:
        raise ValueError(f"Session changed during discovery: {path}")
    return JsonlMetadata(path, session_id, provider if isinstance(provider, str) else None, header, identity)


def discover_metadata(home: Path, progress: Progress) -> MetadataPlan:
    jsonl: list[JsonlMetadata] = []
    rows: list[StateMetadata] = []
    scanned = 0
    for directory in (home / "sessions", home / "archived_sessions"):
        for root, dirs, files in os.walk(directory, followlinks=False):
            dirs[:] = sorted(name for name in dirs if not (Path(root) / name).is_symlink())
            for name in sorted(files):
                path = Path(root) / name
                if path.suffix != ".jsonl":
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(home):
                    raise ValueError(f"Session path must stay inside Codex home: {path}")
                item = read_metadata(path)
                if item is not None:
                    jsonl.append(item)
                scanned += 1
                progress("discovery", scanned)
    for path in sorted(home.glob("state_*.sqlite")):
        if path.is_symlink():
            raise ValueError(f"State database must not be a symlink: {path}")
        identity = file_identity(path)[:2]
        with closing(_connect_sqlite(path, read_only=True)) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(threads)")}
            if not {"id", "model_provider"} <= columns:
                continue
            for session_id, provider in conn.execute("SELECT id, model_provider FROM threads"):
                if isinstance(session_id, str):
                    rows.append(
                        StateMetadata(identity, path, session_id, provider if isinstance(provider, str) else None)
                    )
                scanned += 1
                progress("discovery", scanned)
    progress("discovery", scanned)
    return MetadataPlan(tuple(jsonl), tuple(rows))
