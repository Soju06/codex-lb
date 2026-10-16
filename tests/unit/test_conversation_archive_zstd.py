from __future__ import annotations

import gzip
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import zstandard

from app.core import conversation_archive
from app.modules.conversation_archive import service as conversation_archive_service

pytestmark = pytest.mark.unit


def _write_gzip_members(path: Path, requests: list[str]) -> bytes:
    """Write one gzip member per record, as the batched writer does."""
    content = b""
    for request_id in requests:
        line = (
            json.dumps({"request_id": request_id, "direction": "codex_to_server", "payload": "x" * 200}) + "\n"
        ).encode()
        with gzip.open(path, "ab") as fh:
            fh.write(line)
        content += line
    return content


def _age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _zstd_bytes(path: Path) -> bytes:
    with path.open("rb") as fh:
        return zstandard.ZstdDecompressor().stream_reader(fh, read_across_frames=True).read()


def test_seal_reencodes_closed_hour_and_removes_gzip(tmp_path: Path) -> None:
    source = tmp_path / "2026-04-29T10.jsonl.gz"
    content = _write_gzip_members(source, ["req_1", "req_2", "req_3"])
    _age(source, 3600)

    sealed = conversation_archive.seal_closed_archive_hours(tmp_path, active_stem="2026-04-29T11")

    assert sealed == 1
    assert not source.exists()
    assert _zstd_bytes(tmp_path / "2026-04-29T10.jsonl.zst") == content
    assert list(tmp_path.glob(".*partial")) == []


def test_seal_skips_active_recent_and_future_hours(tmp_path: Path) -> None:
    active = tmp_path / "2026-04-29T11.jsonl.gz"
    recent = tmp_path / "2026-04-29T10.jsonl.gz"
    _write_gzip_members(active, ["a"])
    _write_gzip_members(recent, ["b"])
    _age(active, 3600)

    sealed = conversation_archive.seal_closed_archive_hours(tmp_path, active_stem="2026-04-29T11")

    assert sealed == 0
    assert active.exists()
    assert recent.exists()
    assert list(tmp_path.glob("*.zst")) == []


def test_seal_keeps_gzip_when_verification_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "2026-04-29T10.jsonl.gz"
    _write_gzip_members(source, ["req_1"])
    monkeypatch.setattr(conversation_archive, "_zstd_content_digest", lambda _path: "0" * 64)

    assert conversation_archive.seal_gzip_archive(source) is False

    assert source.exists()
    assert list(tmp_path.glob("*.zst")) == []
    assert list(tmp_path.glob(".*partial")) == []


def test_seal_keeps_corrupt_gzip_untouched(tmp_path: Path) -> None:
    source = tmp_path / "2026-04-29T10.jsonl.gz"
    _write_gzip_members(source, ["req_1"])
    with source.open("ab") as fh:
        fh.write(b"\x1f\x8b\x08truncated-member")
    original = source.read_bytes()

    assert conversation_archive.seal_gzip_archive(source) is False

    assert source.read_bytes() == original
    assert list(tmp_path.glob("*.zst")) == []
    assert list(tmp_path.glob(".*partial")) == []
    assert source in conversation_archive._UNSEALABLE
    _age(source, 3600)
    assert conversation_archive.seal_closed_archive_hours(tmp_path, active_stem="2026-04-29T11") == 0
    conversation_archive._UNSEALABLE.discard(source)


def test_seal_does_not_overwrite_existing_zstd_file(tmp_path: Path) -> None:
    source = tmp_path / "2026-04-29T10.jsonl.gz"
    _write_gzip_members(source, ["req_late"])
    existing = tmp_path / "2026-04-29T10.jsonl.zst"
    existing.write_bytes(b"sealed")

    assert conversation_archive.seal_gzip_archive(source) is False

    assert source.exists()
    assert existing.read_bytes() == b"sealed"


def test_hour_rollover_schedules_one_background_seal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        conversation_archive,
        "_seal_pass",
        lambda directory, *, active_stem, quiet_seconds: calls.append((directory, active_stem)) or (0, None),
    )
    monkeypatch.setattr(conversation_archive, "_SEALED_UP_TO", {})

    conversation_archive._schedule_seal_of_closed_hours(tmp_path / "2026-04-29T11.jsonl.gz")
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    conversation_archive._schedule_seal_of_closed_hours(tmp_path / "2026-04-29T11.jsonl.gz")
    time.sleep(0.1)

    assert calls == [(tmp_path, "2026-04-29T11")]


def test_reader_reads_gzip_and_zstd_hours(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: SimpleNamespace(conversation_archive_dir=tmp_path),
    )
    gzip_path = tmp_path / "2026-04-29T10.jsonl.gz"
    _write_gzip_members(gzip_path, ["req_gz"])
    sealed_path = tmp_path / "2026-04-29T09.jsonl.gz"
    _write_gzip_members(sealed_path, ["req_zst_1", "req_zst_2"])
    assert conversation_archive.seal_gzip_archive(sealed_path)

    page = conversation_archive_service.read_archive_records(filename=None, limit=10, offset=0)
    assert [(r["request_id"], r["_archive_file"]) for r in page.records] == [
        ("req_zst_1", "2026-04-29T09.jsonl.zst"),
        ("req_zst_2", "2026-04-29T09.jsonl.zst"),
        ("req_gz", "2026-04-29T10.jsonl.gz"),
    ]

    by_name = conversation_archive_service.read_archive_records(
        filename="2026-04-29T09.jsonl.zst", limit=10, offset=0, request_id="req_zst_2"
    )
    assert by_name.total == 1

    by_time = conversation_archive_service.read_archive_records(
        filename=None,
        limit=10,
        offset=0,
        requested_at=datetime(2026, 4, 29, 9, 30, tzinfo=UTC),
    )
    assert {r["request_id"] for r in by_time.records} == {"req_zst_1", "req_zst_2", "req_gz"}


def test_reader_skips_truncated_zstd_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: SimpleNamespace(conversation_archive_dir=tmp_path),
    )
    (tmp_path / "2026-04-29T09.jsonl.zst").write_bytes(b"\x28\xb5\x2f\xfd not a frame")

    page = conversation_archive_service.read_archive_records(filename=None, limit=10, offset=0)

    assert page.records == []


def test_seal_keeps_gzip_appended_while_sealing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "2026-04-29T10.jsonl.gz"
    _write_gzip_members(source, ["req_1"])
    original_digest = conversation_archive._zstd_content_digest

    def append_during_seal(path: Path) -> str:
        # A queued append lands after the sealer read the source, before the swap.
        _write_gzip_members(source, ["req_late"])
        return original_digest(path)

    monkeypatch.setattr(conversation_archive, "_zstd_content_digest", append_during_seal)

    assert conversation_archive.seal_gzip_archive(source) is False

    assert source.exists()
    with gzip.open(source, "rb") as fh:
        assert b"req_late" in fh.read()
    assert not (tmp_path / "2026-04-29T10.jsonl.zst").exists()
    assert list(tmp_path.glob(".*partial")) == []

    monkeypatch.setattr(conversation_archive, "_zstd_content_digest", original_digest)
    assert conversation_archive.seal_gzip_archive(source) is True
    assert b"req_late" in _zstd_bytes(tmp_path / "2026-04-29T10.jsonl.zst")


def test_interrupted_switch_reader_reads_one_and_recovery_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: SimpleNamespace(conversation_archive_dir=tmp_path),
    )
    source = tmp_path / "2026-04-29T10.jsonl.gz"
    content = _write_gzip_members(source, ["req_1", "req_2"])
    # Crash between replace(target) and unlink(source): both formats hold the same records.
    (tmp_path / "2026-04-29T10.jsonl.zst").write_bytes(zstandard.ZstdCompressor().compress(content))
    _age(source, 3600)

    page = conversation_archive_service.read_archive_records(filename=None, limit=10, offset=0)
    assert [r["request_id"] for r in page.records] == ["req_1", "req_2"]

    assert conversation_archive.seal_closed_archive_hours(tmp_path, active_stem="2026-04-29T11") == 1

    assert not source.exists()
    assert _zstd_bytes(tmp_path / "2026-04-29T10.jsonl.zst") == content
    page = conversation_archive_service.read_archive_records(filename=None, limit=10, offset=0)
    assert [r["request_id"] for r in page.records] == ["req_1", "req_2"]


def test_late_gzip_after_seal_is_read_and_merged_not_lost(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: SimpleNamespace(conversation_archive_dir=tmp_path),
    )
    source = tmp_path / "2026-04-29T10.jsonl.gz"
    sealed = _write_gzip_members(source, ["req_1"])
    assert conversation_archive.seal_gzip_archive(source)
    late = _write_gzip_members(source, ["req_late"])

    page = conversation_archive_service.read_archive_records(filename=None, limit=10, offset=0)
    assert sorted(r["request_id"] for r in page.records) == ["req_1", "req_late"]

    assert conversation_archive.seal_gzip_archive(source) is True
    assert not source.exists()
    assert _zstd_bytes(tmp_path / "2026-04-29T10.jsonl.zst") == sealed + late


def test_quiet_period_file_is_sealed_on_a_later_pass_without_new_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "2026-04-29T10.jsonl.gz"
    content = _write_gzip_members(source, ["req_1"])
    monkeypatch.setattr(conversation_archive, "_SEALED_UP_TO", {})
    monkeypatch.setattr(conversation_archive, "_SEAL_QUIET_SECONDS", 0.4)
    monkeypatch.setattr(conversation_archive, "_SEAL_RETRY_SLACK_SECONDS", 0.1)

    conversation_archive._schedule_seal_of_closed_hours(tmp_path / "2026-04-29T11.jsonl.gz")
    time.sleep(0.1)
    assert source.exists()  # first pass: still inside the quiet period

    target = tmp_path / "2026-04-29T10.jsonl.zst"
    deadline = time.monotonic() + 10
    while not target.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert target.exists()
    deadline = time.monotonic() + 5
    while source.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not source.exists()
    assert _zstd_bytes(target) == content
