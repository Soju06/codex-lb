from __future__ import annotations

import base64
import errno
import gzip
import json
import os
import queue
import stat
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import cast

from app.core import conversation_archive
from app.core.utils.request_id import reset_request_id, set_request_id
from app.modules.conversation_archive import api as conversation_archive_api
from app.modules.conversation_archive import service as conversation_archive_service


def _reset_archive_disk_pressure() -> None:
    with conversation_archive._DISK_PRESSURE_LOCK:
        conversation_archive._DISK_PRESSURE_PAUSED_UNTIL = 0.0
        conversation_archive._DISK_PRESSURE_LAST_WARNING_AT = (
            -conversation_archive._DISK_PRESSURE_WARNING_INTERVAL_SECONDS
        )


class _ArchiveSettings:
    def __init__(self, *, enabled: bool, directory: Path, queue_max_bytes: int = 8 * 1024 * 1024) -> None:
        self.conversation_archive_enabled = enabled
        self.conversation_archive_dir = directory
        self.conversation_archive_queue_max_bytes = queue_max_bytes


def _archive_records(directory: Path) -> list[dict[str, object]]:
    files = sorted(directory.glob("*.jsonl.gz"))
    assert len(files) == 1
    with gzip.open(files[0], "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh.read().splitlines()]


def _archive_lines(directory: Path) -> list[str]:
    files = sorted(directory.glob("*.jsonl.gz"))
    assert len(files) == 1
    with gzip.open(files[0], "rt", encoding="utf-8") as fh:
        return fh.read().splitlines()


def _write_gzip_record(path: Path, payload: dict[str, object]) -> None:
    with gzip.open(path, "at", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


def test_archive_json_writes_redacted_jsonl_record(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    token = set_request_id("req_archive_1")
    try:
        conversation_archive.archive_json(
            direction="codex_to_server",
            kind="responses",
            transport="http",
            account_id="acc_1",
            method="POST",
            url="https://chatgpt.com/backend-api/codex/responses",
            headers={
                "Authorization": "Bearer secret",
                "api-key": "bare-secret",
                "api_key": "underscore-secret",
                "session_id": "sid_1",
                "x-session-token": "tok_1",
            },
            payload={"model": "gpt-5.4", "input": "привет без юникод-эскейпов"},
        )
    finally:
        reset_request_id(token)
    conversation_archive.flush_archive_writer()

    [record] = _archive_records(tmp_path)
    assert record["request_id"] == "req_archive_1"
    assert record["direction"] == "codex_to_server"
    assert record["kind"] == "responses"
    assert record["transport"] == "http"
    assert record["account_id"] == "acc_1"
    assert record["payload"] == {"model": "gpt-5.4", "input": "привет без юникод-эскейпов"}
    assert record["headers"] == {
        "Authorization": "[redacted]",
        "api-key": "[redacted]",
        "api_key": "[redacted]",
        "session_id": "sid_1",
        "x-session-token": "[redacted]",
    }
    [line] = _archive_lines(tmp_path)
    assert "привет без юникод-эскейпов" in line
    assert "\\u043f" not in line


def test_archive_bytes_disabled_does_not_encode_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=False, directory=tmp_path),
    )

    def fail_b64encode(_data: bytes) -> bytes:
        raise AssertionError("disabled archiving should not encode binary payloads")

    monkeypatch.setattr(conversation_archive.base64, "b64encode", fail_b64encode)

    conversation_archive.archive_bytes(
        direction="server_to_codex",
        kind="responses",
        transport="websocket",
        data=b"large binary frame",
    )

    assert list(tmp_path.iterdir()) == []


def test_archive_queue_is_bounded_and_waits_without_sync_backpressure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )
    bounded_queue: queue.Queue[tuple[Path, dict[str, object], int] | None] = queue.Queue(maxsize=1)
    bounded_queue.put((tmp_path / "blocked.jsonl.gz", {"payload": "queued"}, 10))
    monkeypatch.setattr(conversation_archive, "_WRITE_QUEUE", bounded_queue)
    monkeypatch.setattr(conversation_archive, "_ensure_writer_thread", lambda: None)

    def drain_existing_item() -> None:
        time.sleep(0.01)
        bounded_queue.get()
        bounded_queue.task_done()

    drainer = threading.Thread(target=drain_existing_item)
    drainer.start()
    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "синхронный fallback"},
    )
    drainer.join(timeout=1.0)

    assert list(tmp_path.glob("*.jsonl.gz")) == []
    assert bounded_queue.qsize() == 1
    queued_item = bounded_queue.get_nowait()
    assert queued_item is not None
    _path, record, _queued_bytes = queued_item
    assert record["payload"] == {"text": "синхронный fallback"}


def test_archive_queue_byte_limit_preserves_oversized_record_with_backpressure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path, queue_max_bytes=64),
    )
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        conversation_archive._WRITE_QUEUE_BYTES = 0

    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "x" * 256},
    )
    conversation_archive.flush_archive_writer()

    [record] = _archive_records(tmp_path)
    assert record["payload"] == {"text": "x" * 256}
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        assert conversation_archive._WRITE_QUEUE_BYTES == 0


def test_archive_queue_byte_limit_waits_before_queueing_second_oversized_record(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path, queue_max_bytes=64),
    )
    queued_records: queue.Queue[tuple[Path, dict[str, object], int] | None] = queue.Queue()
    monkeypatch.setattr(conversation_archive, "_WRITE_QUEUE", queued_records)
    monkeypatch.setattr(conversation_archive, "_ensure_writer_thread", lambda: None)
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        conversation_archive._WRITE_QUEUE_BYTES = 0

    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "x" * 256},
    )

    first_item = queued_records.get_nowait()
    assert first_item is not None
    assert first_item[1]["payload"] == {"text": "x" * 256}
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        assert conversation_archive._WRITE_QUEUE_BYTES == first_item[2]

    second_started = threading.Event()

    def enqueue_second() -> None:
        second_started.set()
        conversation_archive.archive_json(
            direction="codex_to_server",
            kind="responses",
            transport="http",
            payload={"text": "y" * 256},
        )

    second_thread = threading.Thread(target=enqueue_second)
    second_thread.start()
    assert second_started.wait(timeout=1.0)
    time.sleep(0.05)
    assert queued_records.empty()
    assert second_thread.is_alive()

    conversation_archive._release_archive_queue_bytes(first_item[2])
    second_thread.join(timeout=1.0)
    assert not second_thread.is_alive()

    second_item = queued_records.get_nowait()
    assert second_item is not None
    assert second_item[1]["payload"] == {"text": "y" * 256}
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        assert conversation_archive._WRITE_QUEUE_BYTES == second_item[2]

    conversation_archive._release_archive_queue_bytes(second_item[2])


def test_archive_queue_byte_limit_warning_is_rate_limited(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path, queue_max_bytes=64),
    )
    monkeypatch.setattr(conversation_archive, "_archive_disk_pressure_active", lambda: False)
    monkeypatch.setattr(conversation_archive, "_reserve_archive_queue_bytes", lambda _size: False)
    monkeypatch.setattr(conversation_archive, "_reserve_archive_queue_bytes_blocking", lambda _size: None)
    monotonic_values = iter([100.0, 101.0, 102.0, 161.0])
    monkeypatch.setattr(conversation_archive.time, "monotonic", lambda: next(monotonic_values))
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        conversation_archive._WRITE_QUEUE_BYTES = 0
    with conversation_archive._BACKPRESSURE_WARNING_LOCK:
        conversation_archive._BACKPRESSURE_LAST_WARNING_AT = (
            -conversation_archive._BACKPRESSURE_WARNING_INTERVAL_SECONDS
        )
        conversation_archive._BACKPRESSURE_SUPPRESSED_WARNINGS = 0

    caplog.set_level("WARNING", logger="app.core.conversation_archive")
    for idx in range(4):
        conversation_archive.archive_json(
            direction="codex_to_server",
            kind="responses",
            transport="http",
            payload={"text": f"{idx}-" + ("x" * 256)},
        )
    conversation_archive.flush_archive_writer()

    warnings = [
        record
        for record in caplog.records
        if "Conversation archive writer queue byte budget is full" in record.getMessage()
    ]
    assert len(warnings) == 2
    assert warnings[0].suppressed_warnings == 0
    assert warnings[1].suppressed_warnings == 2


def test_archive_writer_streams_gzip_without_precompressing_record(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    def fail_compress(_data: bytes) -> bytes:
        raise AssertionError("normal archive writes should stream gzip data")

    monkeypatch.setattr(conversation_archive.gzip, "compress", fail_compress)
    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "x" * 1024},
    )
    conversation_archive.flush_archive_writer()

    [record] = _archive_records(tmp_path)
    assert record["payload"] == {"text": "x" * 1024}


def test_archive_writer_batches_queued_records(monkeypatch, tmp_path):
    archive_queue: queue.Queue[tuple[Path, dict[str, object], int] | None] = queue.Queue()
    first: tuple[Path, dict[str, object], int] = (tmp_path / "archive.jsonl.gz", {"payload": "one"}, 11)
    second: tuple[Path, dict[str, object], int] = (tmp_path / "archive.jsonl.gz", {"payload": "two"}, 13)
    archive_queue.put(first)
    archive_queue.put(second)
    archive_queue.put(None)
    monkeypatch.setattr(conversation_archive, "_WRITE_QUEUE", archive_queue)
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        setattr(conversation_archive, "_WRITE_QUEUE_BYTES", 24)

    batches: list[list[tuple[Path, dict[str, object], int]]] = []

    def capture_batch(items: Sequence[tuple[Path, dict[str, object], int]]) -> None:
        batches.append(list(items))

    monkeypatch.setattr(conversation_archive, "_append_records", capture_batch)

    conversation_archive._writer_loop()

    assert [[item[1]["payload"] for item in batch] for batch in batches] == [["one", "two"]]
    assert archive_queue.unfinished_tasks == 0
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        assert conversation_archive._WRITE_QUEUE_BYTES == 0


def test_archive_queue_thread_start_failure_drops_record(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        conversation_archive._WRITE_QUEUE_BYTES = 0

    def fail_writer_thread() -> None:
        raise RuntimeError("thread limit reached")

    monkeypatch.setattr(conversation_archive, "_ensure_writer_thread", fail_writer_thread)

    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "optional archive"},
    )

    assert list(tmp_path.glob("*.jsonl.gz")) == []
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        assert conversation_archive._WRITE_QUEUE_BYTES == 0


def test_archive_stop_writer_drains_queue_before_sentinel(monkeypatch):
    events: list[str] = []

    class FakeQueue:
        def join(self) -> None:
            events.append("queue.join")

        def put(self, item: object) -> None:
            assert item is None
            events.append("queue.put_sentinel")

    class FakeThread:
        def join(self, timeout: float | None = None) -> None:
            assert timeout is None
            events.append("thread.join")

    monkeypatch.setattr(conversation_archive, "_WRITE_QUEUE", FakeQueue())
    monkeypatch.setattr(conversation_archive, "_WRITER_THREAD", FakeThread())

    conversation_archive._stop_writer()

    assert events == ["queue.join", "queue.put_sentinel", "thread.join"]


def test_archive_file_listing_runs_sync(monkeypatch):
    called: list[object] = []
    modified_at = datetime.fromisoformat("2026-05-16T00:00:00+00:00")
    archive_file = conversation_archive_service.ConversationArchiveFile(
        name="2026-05-16T00.jsonl.gz",
        date="2026-05-16T00",
        size_bytes=123,
        compressed=True,
        modified_at=modified_at,
    )

    monkeypatch.setattr(
        conversation_archive_api.service,
        "list_archive_files",
        lambda: called.append("service") or [archive_file],
    )

    [response] = conversation_archive_api.list_conversation_archive_files()

    assert called == ["service"]
    assert response.name == "2026-05-16T00.jsonl.gz"
    assert response.modified_at == modified_at


def test_archive_appends_complete_gzip_members(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "first"},
    )
    conversation_archive.archive_json(
        direction="server_to_codex",
        kind="responses",
        transport="http",
        payload={"text": "second"},
    )
    conversation_archive.flush_archive_writer()

    records = _archive_records(tmp_path)
    assert [record["payload"] for record in records] == [{"text": "first"}, {"text": "second"}]


def test_archive_uses_hourly_gzip_files(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "hourly"},
    )
    conversation_archive.flush_archive_writer()

    [path] = list(tmp_path.glob("*.jsonl.gz"))
    assert len(path.stem) == len("2026-04-30T12.jsonl")
    assert path.name.endswith(".jsonl.gz")
    assert "T" in path.name


def test_archive_files_are_operator_only_even_with_permissive_umask(monkeypatch, tmp_path):
    archive_dir = tmp_path / "archive"
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=archive_dir),
    )
    old_umask = os.umask(0o022)
    try:
        conversation_archive.archive_json(
            direction="codex_to_server",
            kind="responses",
            transport="http",
            payload={"text": "private"},
        )
        conversation_archive.flush_archive_writer()
    finally:
        os.umask(old_umask)

    [path] = list(archive_dir.glob("*.jsonl.gz"))
    assert stat.S_IMODE(archive_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_archive_path_expands_user_home(monkeypatch, tmp_path):
    archive_dir = tmp_path / "archive"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=Path("~/archive")),
    )

    assert conversation_archive._archive_path().parent == archive_dir


def test_archive_writer_recovers_corrupt_gzip_tail_before_append(tmp_path):
    path = tmp_path / "2026-04-30.jsonl.gz"
    conversation_archive._RECOVERY_CHECKED_PATHS.discard(path.resolve())
    path.write_bytes(
        gzip.compress(json.dumps({"request_id": "req_ok", "payload": "old"}).encode("utf-8") + b"\n")
        + b"not a complete gzip member"
    )

    conversation_archive._append_record(path, {"request_id": "req_new", "payload": "new"})

    with gzip.open(path, "rt", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh.read().splitlines()]
    assert [row["request_id"] for row in rows] == ["req_ok", "req_new"]
    backups = list(tmp_path.glob("2026-04-30.jsonl.gz.corrupt-*"))
    assert len(backups) == 1


def test_archive_recovery_check_runs_once_per_file(monkeypatch, tmp_path):
    path = tmp_path / "2026-04-30T12.jsonl.gz"
    conversation_archive._RECOVERY_CHECKED_PATHS.discard(path.resolve())
    path.write_bytes(gzip.compress(json.dumps({"request_id": "req_ok"}).encode("utf-8") + b"\n"))
    calls = 0

    def fake_readable(read_path: Path) -> bool:
        nonlocal calls
        assert read_path == path
        calls += 1
        return True

    monkeypatch.setattr(conversation_archive, "_gzip_archive_is_readable", fake_readable)

    conversation_archive._append_record(path, {"request_id": "req_new_1"})
    conversation_archive._append_record(path, {"request_id": "req_new_2"})

    assert calls == 1


def test_archive_write_failure_forgets_prior_recovery_check(monkeypatch, tmp_path):
    path = tmp_path / "2026-04-30T12.jsonl.gz"
    path.write_bytes(gzip.compress(json.dumps({"request_id": "req_ok"}).encode("utf-8") + b"\n"))
    conversation_archive._RECOVERY_CHECKED_PATHS.discard(path.resolve())

    conversation_archive._append_record(path, {"request_id": "req_new_1"})
    assert path.resolve() in conversation_archive._RECOVERY_CHECKED_PATHS

    def fail_write(_path: Path, _record: object) -> None:
        raise OSError("simulated partial archive write")

    monkeypatch.setattr(conversation_archive, "_write_gzip_jsonl_record", fail_write)
    conversation_archive._append_record(path, {"request_id": "req_new_2"})

    assert path.resolve() not in conversation_archive._RECOVERY_CHECKED_PATHS


def test_archive_batch_write_failure_does_not_drop_other_paths(monkeypatch, tmp_path):
    bad_path = tmp_path / "bad" / "2026-04-30T12.jsonl.gz"
    good_path = tmp_path / "good" / "2026-04-30T12.jsonl.gz"

    def write_record(path: Path, record: Mapping[str, object]) -> None:
        if path == bad_path:
            raise OSError("simulated archive write failure")
        conversation_archive._write_gzip_jsonl_records(path, [record])

    monkeypatch.setattr(conversation_archive, "_write_gzip_jsonl_record", write_record)

    conversation_archive._append_records(
        [
            (bad_path, {"request_id": "req_bad"}, 0),
            (good_path, {"request_id": "req_good"}, 0),
        ]
    )

    assert not bad_path.exists()
    with gzip.open(good_path, "rt", encoding="utf-8") as handle:
        assert json.loads(handle.readline()) == {"request_id": "req_good"}


def test_archive_disk_full_pauses_writes_without_traceback_spam(monkeypatch, tmp_path, caplog):
    _reset_archive_disk_pressure()
    path = tmp_path / "2026-04-30T12.jsonl.gz"

    def fail_write(_path: Path, _record: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(conversation_archive, "_write_gzip_jsonl_record", fail_write)
    caplog.set_level("WARNING", logger="app.core.conversation_archive")

    conversation_archive._append_record(path, {"request_id": "req_full", "payload": "old"})

    assert not path.exists()
    assert "Conversation archive disk pressure detected; pausing archive writes" in caplog.text
    assert "Traceback" not in caplog.text
    assert conversation_archive._archive_disk_pressure_active() is True

    calls = 0

    def record_write(_path: Path, _record: object) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(conversation_archive, "_write_gzip_jsonl_record", record_write)
    conversation_archive._append_record(path, {"request_id": "req_dropped", "payload": "new"})
    assert calls == 0

    with conversation_archive._DISK_PRESSURE_LOCK:
        conversation_archive._DISK_PRESSURE_PAUSED_UNTIL = 0.0
    conversation_archive._append_record(path, {"request_id": "req_retry", "payload": "new"})
    assert calls == 1


def test_archive_enqueue_drops_records_while_disk_pressure_pause_is_active(monkeypatch, tmp_path):
    _reset_archive_disk_pressure()
    with conversation_archive._DISK_PRESSURE_LOCK:
        conversation_archive._DISK_PRESSURE_PAUSED_UNTIL = float(conversation_archive.time.monotonic() + 60)
    monkeypatch.setattr(
        conversation_archive,
        "_ensure_writer_thread",
        lambda: (_ for _ in ()).throw(AssertionError("writer should not start while paused")),
    )

    conversation_archive._enqueue_record(tmp_path / "paused.jsonl.gz", {"request_id": "req_paused"})

    assert list(tmp_path.iterdir()) == []
    _reset_archive_disk_pressure()


def test_archive_disk_pressure_detection_walks_exception_causes():
    wrapped = RuntimeError("outer")
    wrapped.__cause__ = OSError(errno.EDQUOT, "Disk quota exceeded")

    assert conversation_archive._is_disk_pressure_error(wrapped) is True
    assert conversation_archive._is_disk_pressure_error(RuntimeError("database or disk is full")) is True
    assert conversation_archive._is_disk_pressure_error(RuntimeError("regular archive failure")) is False


def test_archive_disabled_does_not_create_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=False, directory=tmp_path),
    )

    conversation_archive.archive_text(
        direction="server_to_codex",
        kind="responses",
        transport="websocket",
        text='{"type":"response.completed"}',
    )
    conversation_archive.flush_archive_writer()

    assert list(tmp_path.glob("*.jsonl*")) == []


def test_archive_bytes_uses_base64_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    conversation_archive.archive_bytes(
        direction="server_to_codex",
        kind="responses",
        transport="websocket",
        data=b"\x00\x01binary",
    )
    conversation_archive.flush_archive_writer()

    [record] = _archive_records(tmp_path)
    payload = cast(dict[str, object], record["payload"])
    assert payload["encoding"] == "base64"
    assert payload["data"] == base64.b64encode(b"\x00\x01binary").decode("ascii")


def test_archive_service_reads_gzip_and_legacy_jsonl(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )
    legacy_path = tmp_path / "2026-04-28.jsonl"
    legacy_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-28T10:00:00+00:00",
                "request_id": "req_legacy",
                "direction": "codex_to_server",
                "kind": "responses",
                "transport": "http",
                "payload": {"input": "old"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    gzip_path = tmp_path / "2026-04-29T10.jsonl.gz"
    with gzip.open(gzip_path, "wt", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "timestamp": "2026-04-29T10:00:00+00:00",
                    "request_id": "req_gzip",
                    "direction": "server_to_codex",
                    "kind": "responses",
                    "transport": "sse",
                    "payload": {"type": "response.completed"},
                }
            )
            + "\n"
        )

    files = conversation_archive_service.list_archive_files()
    assert [file.name for file in files] == ["2026-04-29T10.jsonl.gz", "2026-04-28.jsonl"]
    assert [file.date for file in files] == ["2026-04-29T10", "2026-04-28"]
    assert files[0].compressed is True
    assert files[1].compressed is False

    page = conversation_archive_service.read_archive_records(
        filename="2026-04-29T10.jsonl.gz",
        limit=10,
        offset=0,
        direction="server_to_codex",
    )
    assert page.total == 1
    assert page.has_more is False
    assert page.records[0]["request_id"] == "req_gzip"
    assert page.records[0]["_archive_file"] == "2026-04-29T10.jsonl.gz"

    legacy_page = conversation_archive_service.read_archive_records(
        filename="2026-04-28.jsonl",
        limit=10,
        offset=0,
        request_id="req_legacy",
    )
    assert legacy_page.total == 1
    assert legacy_page.records[0]["payload"] == {"input": "old"}

    all_files_page = conversation_archive_service.read_archive_records(
        filename=None,
        limit=10,
        offset=0,
        request_id="req_gzip",
    )
    assert [record["request_id"] for record in all_files_page.records] == ["req_gzip"]

    requested_at_page = conversation_archive_service.read_archive_records(
        filename=None,
        limit=10,
        offset=0,
        request_id="req_gzip",
        requested_at=datetime.fromisoformat("2026-04-29T10:30:00+00:00"),
    )
    assert [record["request_id"] for record in requested_at_page.records] == ["req_gzip"]


def test_archive_service_lookup_requests_adjacent_hours(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    _write_gzip_record(
        tmp_path / "2026-04-29T10.jsonl.gz",
        {
            "timestamp": "2026-04-29T10:59:45+00:00",
            "request_id": "req_prev_hour",
            "direction": "server_to_codex",
            "kind": "responses",
            "transport": "http",
            "payload": {"input": "prev"},
        },
    )
    _write_gzip_record(
        tmp_path / "2026-04-29T11.jsonl.gz",
        {
            "timestamp": "2026-04-29T11:00:15+00:00",
            "request_id": "req_next_hour",
            "direction": "server_to_codex",
            "kind": "responses",
            "transport": "http",
            "payload": {"input": "next"},
        },
    )

    boundary_requested_at = datetime.fromisoformat("2026-04-29T11:00:00+00:00")
    page = conversation_archive_service.read_archive_records(
        filename=None,
        limit=10,
        offset=0,
        requested_at=boundary_requested_at,
    )

    assert page.total == 2
    assert [record["request_id"] for record in page.records] == [
        "req_prev_hour",
        "req_next_hour",
    ]
    assert [record["_archive_file"] for record in page.records] == [
        "2026-04-29T10.jsonl.gz",
        "2026-04-29T11.jsonl.gz",
    ]


def test_archive_service_lookup_request_id_searches_beyond_adjacent_hours(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    _write_gzip_record(
        tmp_path / "2026-04-29T10.jsonl.gz",
        {
            "timestamp": "2026-04-29T10:59:45+00:00",
            "request_id": "req_span",
            "direction": "server_to_codex",
            "kind": "responses",
            "transport": "http",
            "payload": {"input": "near"},
        },
    )
    _write_gzip_record(
        tmp_path / "2026-04-29T11.jsonl.gz",
        {
            "timestamp": "2026-04-29T11:00:15+00:00",
            "request_id": "req_other",
            "direction": "server_to_codex",
            "kind": "responses",
            "transport": "http",
            "payload": {"input": "near"},
        },
    )
    _write_gzip_record(
        tmp_path / "2026-04-29T12.jsonl.gz",
        {
            "timestamp": "2026-04-29T12:01:05+00:00",
            "request_id": "req_span",
            "direction": "server_to_codex",
            "kind": "responses",
            "transport": "http",
            "payload": {"input": "later"},
        },
    )

    page = conversation_archive_service.read_archive_records(
        filename=None,
        limit=10,
        offset=0,
        request_id="req_span",
        requested_at=datetime.fromisoformat("2026-04-29T10:30:00+00:00"),
    )

    assert page.total == 2
    assert [record["request_id"] for record in page.records] == ["req_span", "req_span"]
    assert [record["_archive_file"] for record in page.records] == [
        "2026-04-29T10.jsonl.gz",
        "2026-04-29T12.jsonl.gz",
    ]


def test_archive_service_lookup_request_id_includes_adjacent_day_legacy_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    (tmp_path / "2026-04-29.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-04-29T23:59:58+00:00",
                "request_id": "req_midnight",
                "direction": "server_to_codex",
                "kind": "responses",
                "transport": "http",
                "payload": {"input": "end_of_day"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    page = conversation_archive_service.read_archive_records(
        filename=None,
        limit=10,
        offset=0,
        request_id="req_midnight",
        requested_at=datetime.fromisoformat("2026-04-30T00:10:00+00:00"),
    )

    assert page.total == 1
    assert page.records[0]["request_id"] == "req_midnight"
    assert page.records[0]["_archive_file"] == "2026-04-29.jsonl"


def test_archive_service_expands_user_home(monkeypatch, tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    archive_path = archive_dir / "2026-04-29T10.jsonl.gz"
    with gzip.open(archive_path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": "2026-04-29T10:00:00+00:00"}) + "\n")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=Path("~/archive")),
    )

    [file] = conversation_archive_service.list_archive_files()
    assert file.name == archive_path.name


def test_archive_service_keeps_readable_records_before_corrupt_gzip_tail(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )
    path = tmp_path / "2026-04-30.jsonl.gz"
    path.write_bytes(
        gzip.compress(json.dumps({"request_id": "req_ok", "direction": "codex_to_server"}).encode("utf-8") + b"\n")
        + b"not a complete gzip member"
    )

    page = conversation_archive_service.read_archive_records(
        filename=path.name,
        limit=10,
        offset=0,
    )

    assert [record["request_id"] for record in page.records] == ["req_ok"]


def test_archive_service_rejects_path_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    try:
        conversation_archive_service.read_archive_records(
            filename="../2026-04-29.jsonl.gz",
            limit=10,
            offset=0,
        )
    except conversation_archive_service.ConversationArchiveInvalidFileError:
        pass
    else:
        raise AssertionError("expected invalid archive file error")
