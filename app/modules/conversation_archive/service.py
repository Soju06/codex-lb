from __future__ import annotations

import gzip
import json
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.config.settings import get_settings

_JSONL_SUFFIX = ".jsonl"
_GZIP_JSONL_SUFFIX = ".jsonl.gz"


@dataclass(frozen=True)
class ConversationArchiveFile:
    name: str
    date: str | None
    size_bytes: int
    compressed: bool
    modified_at: datetime


@dataclass(frozen=True)
class ConversationArchivePage:
    records: list[dict[str, Any]]
    total: int
    has_more: bool


class ConversationArchiveNotFoundError(ValueError):
    pass


class ConversationArchiveInvalidFileError(ValueError):
    pass


def list_archive_files() -> list[ConversationArchiveFile]:
    directory = _archive_dir()
    if not directory.exists():
        return []

    files: list[ConversationArchiveFile] = []
    for path in sorted(_iter_archive_paths(directory), key=lambda item: item.name, reverse=True):
        stat = path.stat()
        files.append(
            ConversationArchiveFile(
                name=path.name,
                date=_date_from_filename(path.name),
                size_bytes=stat.st_size,
                compressed=path.name.endswith(_GZIP_JSONL_SUFFIX),
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            )
        )
    return files


def read_archive_records(
    *,
    filename: str | None,
    limit: int,
    offset: int,
    direction: str | None = None,
    kind: str | None = None,
    transport: str | None = None,
    request_id: str | None = None,
    requested_at: datetime | None = None,
) -> ConversationArchivePage:
    paths = _archive_paths_for_lookup(filename=filename, requested_at=requested_at)
    records: list[dict[str, Any]] = []
    total = 0
    end = offset + limit

    for path in sorted(paths, key=lambda item: item.name, reverse=True):
        for record in _iter_records(path):
            if not _record_matches(
                record,
                direction=direction,
                kind=kind,
                transport=transport,
                request_id=request_id,
            ):
                continue
            if offset <= total < end:
                records.append({**record, "_archive_file": path.name})
            total += 1

    return ConversationArchivePage(
        records=records,
        total=total,
        has_more=end < total,
    )


def _archive_paths_for_lookup(*, filename: str | None, requested_at: datetime | None) -> list[Path]:
    if filename:
        return [_resolve_archive_file(filename)]
    directory = _archive_dir()
    if requested_at is None:
        return list(_iter_archive_paths(directory))

    requested_at_utc = requested_at.astimezone(UTC)
    hourly_stems = [
        (requested_at_utc + timedelta(hours=delta)).strftime("%Y-%m-%dT%H") for delta in (-1, 0, 1)
    ]
    daily_stem = requested_at_utc.strftime("%Y-%m-%d")
    candidates: list[Path] = []
    for stem in hourly_stems:
        candidates.extend(
            (
                directory / f"{stem}{_GZIP_JSONL_SUFFIX}",
                directory / f"{stem}{_JSONL_SUFFIX}",
            )
        )
    candidates.extend(
        (
            directory / f"{daily_stem}{_GZIP_JSONL_SUFFIX}",
            directory / f"{daily_stem}{_JSONL_SUFFIX}",
        )
    )
    return [path for path in candidates if path.exists() and path.is_file()]


def _archive_dir() -> Path:
    return Path(getattr(get_settings(), "conversation_archive_dir")).expanduser()


def _iter_archive_paths(directory: Path) -> Iterator[Path]:
    yield from directory.glob(f"*{_JSONL_SUFFIX}")
    yield from directory.glob(f"*{_GZIP_JSONL_SUFFIX}")


def _date_from_filename(filename: str) -> str | None:
    if filename.endswith(_GZIP_JSONL_SUFFIX):
        stem = filename[: -len(_GZIP_JSONL_SUFFIX)]
    elif filename.endswith(_JSONL_SUFFIX):
        stem = filename[: -len(_JSONL_SUFFIX)]
    else:
        return None
    for date_format in ("%Y-%m-%dT%H", "%Y-%m-%d"):
        try:
            datetime.strptime(stem, date_format)
        except ValueError:
            continue
        return stem
    return None


def _resolve_archive_file(filename: str) -> Path:
    if Path(filename).name != filename or not (
        filename.endswith(_JSONL_SUFFIX) or filename.endswith(_GZIP_JSONL_SUFFIX)
    ):
        raise ConversationArchiveInvalidFileError("Invalid conversation archive file name")

    path = _archive_dir() / filename
    if not path.exists() or not path.is_file():
        raise ConversationArchiveNotFoundError("Conversation archive file not found")
    return path


def _iter_records(path: Path) -> Iterator[dict[str, Any]]:
    opener = gzip.open if path.name.endswith(_GZIP_JSONL_SUFFIX) else Path.open
    try:
        with opener(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    yield parsed
    except (EOFError, gzip.BadGzipFile, zlib.error):
        return


def _record_matches(
    record: dict[str, Any],
    *,
    direction: str | None,
    kind: str | None,
    transport: str | None,
    request_id: str | None,
) -> bool:
    if direction and record.get("direction") != direction:
        return False
    if kind and record.get("kind") != kind:
        return False
    if transport and record.get("transport") != transport:
        return False
    if request_id and str(record.get("request_id") or "") != request_id:
        return False
    return True
