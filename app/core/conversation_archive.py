from __future__ import annotations

import atexit
import base64
import errno
import gzip
import json
import logging
import os
import queue
import threading
import time
import zlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config.settings import get_settings
from app.core.utils.request_id import get_request_id

logger = logging.getLogger(__name__)

_REDACTED = "[redacted]"
_SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "api-key",
        "api_key",
        "cookie",
        "openai-api-key",
        "proxy-authorization",
        "set-cookie",
        "x-api-key",
    }
)
_WRITE_LOCK = threading.Lock()
_WRITER_LOCK = threading.Lock()
_WRITE_QUEUE_MAX_RECORDS = 4096
_WRITE_QUEUE_MAX_BYTES = 8 * 1024 * 1024
_WRITE_QUEUE_BYTES = 0
_WRITE_QUEUE_BYTES_LOCK = threading.Lock()
_WRITE_QUEUE_BYTES_CONDITION = threading.Condition(_WRITE_QUEUE_BYTES_LOCK)
_WRITE_QUEUE: queue.Queue[tuple[Path, dict[str, Any], int] | None] = queue.Queue(maxsize=_WRITE_QUEUE_MAX_RECORDS)
_WRITER_THREAD: threading.Thread | None = None
_BACKPRESSURE_WARNING_INTERVAL_SECONDS = 60.0
_BACKPRESSURE_LAST_WARNING_AT = -_BACKPRESSURE_WARNING_INTERVAL_SECONDS
_BACKPRESSURE_SUPPRESSED_WARNINGS = 0
_BACKPRESSURE_WARNING_LOCK = threading.Lock()
_RECOVERY_CHECKED_PATHS: set[Path] = set()
_DISK_PRESSURE_PAUSE_SECONDS = 60.0
_DISK_PRESSURE_WARNING_INTERVAL_SECONDS = 300.0
_DISK_PRESSURE_PAUSED_UNTIL = 0.0
_DISK_PRESSURE_LAST_WARNING_AT = -_DISK_PRESSURE_WARNING_INTERVAL_SECONDS
_DISK_PRESSURE_LOCK = threading.Lock()
_ARCHIVE_DIR_MODE = 0o700
_ARCHIVE_FILE_MODE = 0o600


def archive_enabled() -> bool:
    return bool(getattr(get_settings(), "conversation_archive_enabled", False))


def archive_json(
    *,
    direction: str,
    kind: str,
    transport: str,
    payload: Any,
    account_id: str | None = None,
    method: str | None = None,
    url: str | None = None,
    status_code: int | None = None,
    headers: Mapping[str, str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    if not archive_enabled():
        return

    record: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "request_id": get_request_id(),
        "direction": direction,
        "kind": kind,
        "transport": transport,
        "account_id": account_id,
        "method": method,
        "url": url,
        "status_code": status_code,
        "headers": _redact_headers(headers),
        "payload": payload,
    }
    if extra:
        record["extra"] = dict(extra)
    _enqueue_record(_archive_path(), record)


def archive_text(
    *,
    direction: str,
    kind: str,
    transport: str,
    text: str,
    account_id: str | None = None,
    method: str | None = None,
    url: str | None = None,
    status_code: int | None = None,
    headers: Mapping[str, str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    archive_json(
        direction=direction,
        kind=kind,
        transport=transport,
        payload={"text": text},
        account_id=account_id,
        method=method,
        url=url,
        status_code=status_code,
        headers=headers,
        extra=extra,
    )


def archive_bytes(
    *,
    direction: str,
    kind: str,
    transport: str,
    data: bytes,
    account_id: str | None = None,
    method: str | None = None,
    url: str | None = None,
    status_code: int | None = None,
    headers: Mapping[str, str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    if not archive_enabled():
        return

    archive_json(
        direction=direction,
        kind=kind,
        transport=transport,
        payload={
            "encoding": "base64",
            "data": base64.b64encode(data).decode("ascii"),
        },
        account_id=account_id,
        method=method,
        url=url,
        status_code=status_code,
        headers=headers,
        extra=extra,
    )


def _redact_headers(headers: Mapping[str, str] | None) -> dict[str, str] | None:
    if headers is None:
        return None
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        redacted[key] = _redact_header_value(key, value)
    return redacted


def _archive_path() -> Path:
    settings = get_settings()
    directory = Path(getattr(settings, "conversation_archive_dir")).expanduser()
    filename = f"{datetime.now(UTC).strftime('%Y-%m-%dT%H')}.jsonl.gz"
    return directory / filename


def flush_archive_writer() -> None:
    _WRITE_QUEUE.join()


def _enqueue_record(path: Path, record: dict[str, Any]) -> None:
    if _archive_disk_pressure_active():
        return
    queued_bytes = _serialized_record_size(record)
    if not _reserve_archive_queue_bytes(queued_bytes):
        should_warn, suppressed_warnings = _archive_backpressure_warning_state()
        if should_warn:
            logger.warning(
                "Conversation archive writer queue byte budget is full; waiting for async archive writer capacity",
                extra={
                    "queue_max_bytes": _archive_queue_max_bytes(),
                    "record_bytes": queued_bytes,
                    "suppressed_warnings": suppressed_warnings,
                },
            )
        _reserve_archive_queue_bytes_blocking(queued_bytes)
    queued = False
    try:
        _ensure_writer_thread()
        _WRITE_QUEUE.put_nowait((path, record, queued_bytes))
        queued = True
    except queue.Full:
        logger.warning(
            "Conversation archive writer queue is full; waiting for async archive writer capacity",
            extra={"queue_max_records": _WRITE_QUEUE_MAX_RECORDS},
        )
        _WRITE_QUEUE.put((path, record, queued_bytes))
        queued = True
    except Exception:
        logger.warning("Failed to enqueue conversation archive record; dropping it", exc_info=True)
    finally:
        if not queued:
            _release_archive_queue_bytes(queued_bytes)


def _ensure_writer_thread() -> None:
    global _WRITER_THREAD
    if _WRITER_THREAD is not None and _WRITER_THREAD.is_alive():
        return
    with _WRITER_LOCK:
        if _WRITER_THREAD is not None and _WRITER_THREAD.is_alive():
            return
        _WRITER_THREAD = threading.Thread(
            target=_writer_loop,
            name="conversation-archive-writer",
            daemon=True,
        )
        _WRITER_THREAD.start()


def _writer_loop() -> None:
    while True:
        item = _WRITE_QUEUE.get()
        batch: list[tuple[Path, dict[str, Any], int]] = []
        stop_after_batch = False
        try:
            if item is None:
                _WRITE_QUEUE.task_done()
                return
            batch.append(item)
            while True:
                try:
                    next_item = _WRITE_QUEUE.get_nowait()
                except queue.Empty:
                    break
                if next_item is None:
                    stop_after_batch = True
                    _WRITE_QUEUE.task_done()
                    break
                batch.append(next_item)
            _append_records(batch)
        finally:
            for queued_item in batch:
                _release_archive_queue_bytes(queued_item[2])
                _WRITE_QUEUE.task_done()
        if stop_after_batch:
            return


def _append_record(path: Path, record: Mapping[str, Any]) -> None:
    _append_records([(path, record, 0)])


def _append_records(items: Sequence[tuple[Path, Mapping[str, Any], int]]) -> None:
    if not items:
        return
    if _archive_disk_pressure_active():
        return

    grouped: dict[Path, list[Mapping[str, Any]]] = {}
    for path, record, _queued_bytes in items:
        grouped.setdefault(path, []).append(record)

    with _WRITE_LOCK:
        if _archive_disk_pressure_active():
            return
        for path, records in grouped.items():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.parent.chmod(_ARCHIVE_DIR_MODE)
                _recover_corrupt_gzip_archive(path)
                if len(records) == 1:
                    _write_gzip_jsonl_record(path, records[0])
                else:
                    _write_gzip_jsonl_records(path, records)
            except Exception as exc:
                _RECOVERY_CHECKED_PATHS.discard(path.resolve())
                if _is_disk_pressure_error(exc):
                    _pause_archive_for_disk_pressure(path, exc)
                    return
                logger.warning("Failed to append conversation archive record", exc_info=True)


def _serialized_record_size(record: Mapping[str, Any]) -> int:
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return len(line.encode("utf-8")) + 1


def _estimated_record_size(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 4
    if isinstance(value, int | float):
        return len(str(value))
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, bytes | bytearray | memoryview):
        return len(value)
    if isinstance(value, Mapping):
        return 2 + sum(_estimated_record_size(key) + _estimated_record_size(item) + 2 for key, item in value.items())
    if isinstance(value, list | tuple):
        return 2 + sum(_estimated_record_size(item) + 1 for item in value)
    return len(str(value).encode("utf-8"))


def _reserve_archive_queue_bytes(size: int) -> bool:
    global _WRITE_QUEUE_BYTES
    with _WRITE_QUEUE_BYTES_LOCK:
        if not _can_reserve_archive_queue_bytes(size):
            return False
        _WRITE_QUEUE_BYTES += size
        return True


def _reserve_archive_queue_bytes_blocking(size: int) -> None:
    global _WRITE_QUEUE_BYTES
    with _WRITE_QUEUE_BYTES_CONDITION:
        while not _can_reserve_archive_queue_bytes(size):
            _WRITE_QUEUE_BYTES_CONDITION.wait(timeout=0.1)
        _WRITE_QUEUE_BYTES += size


def _can_reserve_archive_queue_bytes(size: int) -> bool:
    max_bytes = _archive_queue_max_bytes()
    if _WRITE_QUEUE_BYTES + size <= max_bytes:
        return True
    return size > max_bytes and _WRITE_QUEUE_BYTES == 0


def _release_archive_queue_bytes(size: int) -> None:
    global _WRITE_QUEUE_BYTES
    with _WRITE_QUEUE_BYTES_CONDITION:
        _WRITE_QUEUE_BYTES = max(0, _WRITE_QUEUE_BYTES - size)
        _WRITE_QUEUE_BYTES_CONDITION.notify_all()


def _archive_queue_max_bytes() -> int:
    configured = getattr(get_settings(), "conversation_archive_queue_max_bytes", _WRITE_QUEUE_MAX_BYTES)
    return max(1, int(configured))


def _archive_backpressure_warning_state() -> tuple[bool, int]:
    global _BACKPRESSURE_LAST_WARNING_AT, _BACKPRESSURE_SUPPRESSED_WARNINGS
    now = time.monotonic()
    with _BACKPRESSURE_WARNING_LOCK:
        if now - _BACKPRESSURE_LAST_WARNING_AT >= _BACKPRESSURE_WARNING_INTERVAL_SECONDS:
            suppressed = _BACKPRESSURE_SUPPRESSED_WARNINGS
            _BACKPRESSURE_SUPPRESSED_WARNINGS = 0
            _BACKPRESSURE_LAST_WARNING_AT = now
            return True, suppressed
        _BACKPRESSURE_SUPPRESSED_WARNINGS += 1
        return False, 0


def _write_gzip_jsonl_record(path: Path, record: Mapping[str, Any]) -> None:
    _write_gzip_jsonl_records(path, [record])


def _write_gzip_jsonl_records(path: Path, records: list[Mapping[str, Any]]) -> None:
    if not records:
        return
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, _ARCHIVE_FILE_MODE)
    try:
        os.fchmod(fd, _ARCHIVE_FILE_MODE)
        with os.fdopen(fd, "ab") as fh:
            fd = -1
            with gzip.open(fh, "at", encoding="utf-8") as gzip_fh:
                for record in records:
                    json.dump(record, gzip_fh, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                    gzip_fh.write("\n")
    finally:
        if fd >= 0:
            os.close(fd)


def _archive_disk_pressure_active() -> bool:
    with _DISK_PRESSURE_LOCK:
        return time.monotonic() < _DISK_PRESSURE_PAUSED_UNTIL


def _pause_archive_for_disk_pressure(path: Path, exc: BaseException) -> None:
    global _DISK_PRESSURE_LAST_WARNING_AT, _DISK_PRESSURE_PAUSED_UNTIL
    now = time.monotonic()
    with _DISK_PRESSURE_LOCK:
        _DISK_PRESSURE_PAUSED_UNTIL = max(_DISK_PRESSURE_PAUSED_UNTIL, now + _DISK_PRESSURE_PAUSE_SECONDS)
        should_warn = now - _DISK_PRESSURE_LAST_WARNING_AT >= _DISK_PRESSURE_WARNING_INTERVAL_SECONDS
        if should_warn:
            _DISK_PRESSURE_LAST_WARNING_AT = now

    if should_warn:
        logger.warning(
            "Conversation archive disk pressure detected; pausing archive writes",
            extra={
                "archive_file": str(path),
                "pause_seconds": _DISK_PRESSURE_PAUSE_SECONDS,
                "error": str(exc),
            },
        )


def _is_disk_pressure_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno in {errno.ENOSPC, errno.EDQUOT}:
            return True
        message = str(current).lower()
        if (
            "no space left on device" in message
            or "disk quota exceeded" in message
            or "database or disk is full" in message
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _recover_corrupt_gzip_archive(path: Path) -> None:
    resolved_path = path.resolve()
    if resolved_path in _RECOVERY_CHECKED_PATHS:
        return

    if not path.exists() or path.stat().st_size == 0:
        return
    if _gzip_archive_is_readable(path):
        _RECOVERY_CHECKED_PATHS.add(resolved_path)
        return

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.corrupt-{timestamp}")
    recovered = path.with_name(f".{path.name}.recovered-{timestamp}")
    recovered_count = 0
    try:
        with gzip.open(path, "rb") as source, recovered.open("wb") as target:
            while True:
                try:
                    line = source.readline()
                except (EOFError, gzip.BadGzipFile, zlib.error):
                    break
                if not line:
                    break
                target.write(gzip.compress(line))
                recovered_count += 1
        path.replace(backup)
        recovered.replace(path)
        _RECOVERY_CHECKED_PATHS.add(resolved_path)
        logger.warning(
            "Recovered readable conversation archive prefix from corrupt gzip file",
            extra={
                "archive_file": str(path),
                "backup_file": str(backup),
                "recovered_records": recovered_count,
            },
        )
    except Exception:
        recovered.unlink(missing_ok=True)
        logger.warning("Failed to recover corrupt conversation archive gzip", exc_info=True)


def _gzip_archive_is_readable(path: Path) -> bool:
    try:
        with gzip.open(path, "rb") as fh:
            for _chunk in iter(lambda: fh.read(1024 * 1024), b""):
                pass
    except (EOFError, gzip.BadGzipFile, zlib.error):
        return False
    return True


def _stop_writer() -> None:
    thread = _WRITER_THREAD
    if thread is None:
        return
    _WRITE_QUEUE.join()
    _WRITE_QUEUE.put(None)
    thread.join()


def _redact_header_value(key: str, value: object) -> str:
    lowered = key.lower()
    normalized = lowered.replace("_", "-")
    if normalized in _SENSITIVE_HEADER_NAMES or normalized.endswith("-api-key") or "token" in normalized:
        return _REDACTED
    return str(value)


atexit.register(_stop_writer)
