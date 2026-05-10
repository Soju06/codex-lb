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
    paths = _archive_paths_for_lookup(filename=filename, requested_at=requested_at, request_id=request_id)
    sorted_paths = sorted(paths, key=lambda item: item.name, reverse=True)
    correlated_request_ids = _correlated_archive_request_ids(sorted_paths, request_id)
    records: list[dict[str, Any]] = []
    total = 0
    end = offset + limit

    for path in sorted_paths:
        for record in _iter_records(path):
            if not _record_matches(
                record,
                direction=direction,
                kind=kind,
                transport=transport,
                request_id=request_id,
                correlated_request_ids=correlated_request_ids,
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


def _archive_paths_for_lookup(
    *,
    filename: str | None,
    requested_at: datetime | None,
    request_id: str | None,
) -> list[Path]:
    if filename:
        return [_resolve_archive_file(filename)]
    directory = _archive_dir()
    if requested_at is None:
        return list(_iter_archive_paths(directory))

    requested_at_utc = requested_at.astimezone(UTC)
    candidate_stems: list[str] = []
    for hour_offset in (-1, 0, 1):
        candidate = requested_at_utc + timedelta(hours=hour_offset)
        hourly_stem = candidate.strftime("%Y-%m-%dT%H")
        daily_stem = candidate.strftime("%Y-%m-%d")
        if hourly_stem not in candidate_stems:
            candidate_stems.append(hourly_stem)
        if daily_stem not in candidate_stems:
            candidate_stems.append(daily_stem)

    candidates = [
        directory / f"{stem}{suffix}" for stem in candidate_stems for suffix in (_GZIP_JSONL_SUFFIX, _JSONL_SUFFIX)
    ]
    existing_candidates = [path for path in candidates if path.exists() and path.is_file()]
    if not request_id:
        return existing_candidates

    candidate_set = set(existing_candidates)
    remaining_paths = [path for path in _iter_archive_paths(directory) if path not in candidate_set]
    return existing_candidates + remaining_paths


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
    correlated_request_ids: set[str],
) -> bool:
    if direction and record.get("direction") != direction:
        return False
    if kind and record.get("kind") != kind:
        return False
    if transport and record.get("transport") != transport:
        return False
    if (
        request_id
        and request_id not in _record_lookup_ids(record)
        and record.get("request_id") not in correlated_request_ids
    ):
        return False
    return True


def _correlated_archive_request_ids(
    paths: list[Path],
    request_id: str | None,
) -> set[str]:
    if not request_id:
        return set()
    correlated: set[str] = set()
    for path in paths:
        for record in _iter_records(path):
            if request_id not in _record_lookup_ids(record):
                continue
            _add_lookup_id(correlated, record.get("request_id"))
    return correlated


def _record_lookup_ids(record: dict[str, Any]) -> set[str]:
    lookup_ids: set[str] = set()
    _add_lookup_id(lookup_ids, record.get("request_id"))
    _add_payload_lookup_ids(lookup_ids, record.get("payload"))
    return lookup_ids


def _add_payload_lookup_ids(lookup_ids: set[str], payload: Any) -> None:
    if isinstance(payload, dict):
        _add_lookup_id(lookup_ids, payload.get("id"))
        response = payload.get("response")
        if isinstance(response, dict):
            _add_lookup_id(lookup_ids, response.get("id"))
        text = payload.get("text")
        if isinstance(text, str):
            _add_text_lookup_ids(lookup_ids, text)


def _add_text_lookup_ids(lookup_ids: set[str], text: str) -> None:
    stripped = text.strip()
    if stripped.startswith("{"):
        _add_payload_lookup_ids(lookup_ids, _parse_json_object(stripped))

    for line in stripped.splitlines():
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            continue
        _add_payload_lookup_ids(lookup_ids, _parse_json_object(data))


def _parse_json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _add_lookup_id(lookup_ids: set[str], value: Any) -> None:
    if isinstance(value, str) and value:
        lookup_ids.add(value)
