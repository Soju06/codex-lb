from __future__ import annotations

import atexit
import base64
import errno
import gzip
import json
import logging
import queue
import threading
import time
import zlib
from collections.abc import Mapping
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
_WRITE_QUEUE: queue.Queue[tuple[Path, dict[str, Any]] | None] = queue.Queue(maxsize=_WRITE_QUEUE_MAX_RECORDS)
_WRITER_THREAD: threading.Thread | None = None
_RECOVERY_CHECKED_PATHS: set[Path] = set()
_DISK_PRESSURE_PAUSE_SECONDS = 60.0
_DISK_PRESSURE_WARNING_INTERVAL_SECONDS = 300.0
_DISK_PRESSURE_PAUSED_UNTIL = 0.0
_DISK_PRESSURE_LAST_WARNING_AT = -_DISK_PRESSURE_WARNING_INTERVAL_SECONDS
_DISK_PRESSURE_LOCK = threading.Lock()


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
    directory = Path(getattr(settings, "conversation_archive_dir"))
    filename = f"{datetime.now(UTC).strftime('%Y-%m-%dT%H')}.jsonl.gz"
    return directory / filename


def flush_archive_writer() -> None:
    _WRITE_QUEUE.join()


def _enqueue_record(path: Path, record: dict[str, Any]) -> None:
    if _archive_disk_pressure_active():
        return
    _ensure_writer_thread()
    try:
        _WRITE_QUEUE.put_nowait((path, record))
    except queue.Full:
        logger.warning(
            "Conversation archive writer queue is full; applying synchronous archive write backpressure",
            extra={"queue_max_records": _WRITE_QUEUE_MAX_RECORDS},
        )
        _append_record(path, record)


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
        try:
            if item is None:
                return
            path, record = item
            _append_record(path, record)
        finally:
            _WRITE_QUEUE.task_done()


def _append_record(path: Path, record: Mapping[str, Any]) -> None:
    if _archive_disk_pressure_active():
        return

    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    data = gzip.compress(f"{line}\n".encode("utf-8"))
    try:
        with _WRITE_LOCK:
            if _archive_disk_pressure_active():
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            _recover_corrupt_gzip_archive(path)
            _write_gzip_member(path, data)
    except Exception as exc:
        if _is_disk_pressure_error(exc):
            _pause_archive_for_disk_pressure(path, exc)
            return
        logger.warning("Failed to append conversation archive record", exc_info=True)


def _write_gzip_member(path: Path, data: bytes) -> None:
    with path.open("ab") as fh:
        fh.write(data)


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
    _RECOVERY_CHECKED_PATHS.add(resolved_path)

    if not path.exists() or path.stat().st_size == 0:
        return
    if _gzip_archive_is_readable(path):
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
    _WRITE_QUEUE.put(None)
    thread.join(timeout=1)


def _redact_header_value(key: str, value: object) -> str:
    lowered = key.lower()
    if lowered in _SENSITIVE_HEADER_NAMES or lowered.endswith("-api-key") or "token" in lowered:
        return _REDACTED
    return str(value)


atexit.register(_stop_writer)
