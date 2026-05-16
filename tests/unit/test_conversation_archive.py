from __future__ import annotations

import base64
import errno
import gzip
import json
import queue
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
    def __init__(self, *, enabled: bool, directory: Path) -> None:
        self.conversation_archive_enabled = enabled
        self.conversation_archive_dir = directory


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


def test_archive_queue_is_bounded_and_falls_back_to_sync_write(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )
    bounded_queue: queue.Queue[tuple[Path, dict[str, object], int] | None] = queue.Queue(maxsize=1)
    bounded_queue.put((tmp_path / "blocked.jsonl.gz", {"payload": "queued"}, 10))
    monkeypatch.setattr(conversation_archive, "_WRITE_QUEUE", bounded_queue)
    monkeypatch.setattr(conversation_archive, "_ensure_writer_thread", lambda: None)

    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "синхронный fallback"},
    )

    [line] = _archive_lines(tmp_path)
    assert "синхронный fallback" in line
    assert bounded_queue.qsize() == 1


def test_archive_queue_byte_limit_falls_back_to_sync_write(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )
    monkeypatch.setattr(conversation_archive, "_WRITE_QUEUE_MAX_BYTES", 64)
    monkeypatch.setattr(
        conversation_archive,
        "_ensure_writer_thread",
        lambda: (_ for _ in ()).throw(AssertionError("writer should not start for byte-budget fallback")),
    )
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        conversation_archive._WRITE_QUEUE_BYTES = 0

    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "x" * 256},
    )

    [record] = _archive_records(tmp_path)
    assert record["payload"] == {"text": "x" * 256}
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

    def fail_write(_path: Path, _data: bytes) -> None:
        raise OSError("simulated partial archive write")

    monkeypatch.setattr(conversation_archive, "_write_gzip_member", fail_write)
    conversation_archive._append_record(path, {"request_id": "req_new_2"})

    assert path.resolve() not in conversation_archive._RECOVERY_CHECKED_PATHS


def test_archive_disk_full_pauses_writes_without_traceback_spam(monkeypatch, tmp_path, caplog):
    _reset_archive_disk_pressure()
    path = tmp_path / "2026-04-30T12.jsonl.gz"

    def fail_write(_path: Path, _data: bytes) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(conversation_archive, "_write_gzip_member", fail_write)
    caplog.set_level("WARNING", logger="app.core.conversation_archive")

    conversation_archive._append_record(path, {"request_id": "req_full", "payload": "old"})

    assert not path.exists()
    assert "Conversation archive disk pressure detected; pausing archive writes" in caplog.text
    assert "Traceback" not in caplog.text
    assert conversation_archive._archive_disk_pressure_active() is True

    calls = 0

    def record_write(_path: Path, _data: bytes) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(conversation_archive, "_write_gzip_member", record_write)
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
