from __future__ import annotations

import errno
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app import codex_sessions_retag
from app.codex_sessions_retag import (
    ProviderCount,
    preview_session_metadata_mismatches,
    repair_session_metadata_mismatches,
    retag_codex_sessions,
)

pytestmark = pytest.mark.unit


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def _create_state_db(path: Path, providers: list[str]) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT)")
        conn.executemany(
            "INSERT INTO threads (id, model_provider) VALUES (?, ?)",
            [(f"thread-{index}", provider) for index, provider in enumerate(providers)],
        )
        conn.commit()


def _read_state_providers(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as conn:
        return [row[0] for row in conn.execute("SELECT model_provider FROM threads ORDER BY id").fetchall()]


def test_targeted_metadata_repair_updates_only_sqlite_mismatch_and_preserves_unrelated_session(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    target_id = "019f7249-90ee-74c0-a0e6-42117e80bc7f"
    unrelated_id = "019f7249-90ee-74c0-a0e6-42117e80bc70"
    target_jsonl = codex_home / "sessions" / "2026" / f"{target_id}.jsonl"
    unrelated_jsonl = codex_home / "sessions" / "2026" / f"{unrelated_id}.jsonl"
    state_db = codex_home / "state_5.sqlite"
    _write_jsonl(target_jsonl, [{"type": "session_meta", "payload": {"id": target_id, "model_provider": "codex-lb"}}])
    _write_jsonl(
        unrelated_jsonl,
        [{"type": "session_meta", "payload": {"id": unrelated_id, "model_provider": "openai"}}],
    )
    with closing(sqlite3.connect(state_db)) as conn:
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT)")
        conn.executemany(
            "INSERT INTO threads (id, model_provider) VALUES (?, ?)",
            [(target_id, "openai"), (unrelated_id, "openai")],
        )
        conn.commit()

    preview = preview_session_metadata_mismatches(codex_home=codex_home, active_provider="codex-lb")

    assert [item.session_id for item in preview.mismatches] == [target_id]
    assert preview.mismatches[0].jsonl_paths == ()
    assert preview.mismatches[0].sqlite_db_paths == (state_db,)

    result = repair_session_metadata_mismatches(
        codex_home=codex_home,
        active_provider="codex-lb",
        session_ids=[target_id],
    )

    assert result.sqlite_rows_updated == 1
    assert result.jsonl_files_updated == 0
    assert result.backup_path is not None
    with closing(sqlite3.connect(state_db)) as conn:
        rows = dict(conn.execute("SELECT id, model_provider FROM threads").fetchall())
    assert rows == {target_id: "codex-lb", unrelated_id: "openai"}
    assert json.loads(target_jsonl.read_text(encoding="utf-8"))["payload"]["model_provider"] == "codex-lb"
    assert json.loads(unrelated_jsonl.read_text(encoding="utf-8"))["payload"]["model_provider"] == "openai"


def test_targeted_metadata_preview_rejects_unsupported_jsonl_provider_tag(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    session_id = "019f7249-90ee-74c0-a0e6-42117e80bc7f"
    _write_jsonl(
        codex_home / "sessions" / "2026" / "active.jsonl",
        [{"type": "session_meta", "payload": {"id": session_id, "model_provider": "codex-lb"}}],
    )
    _write_jsonl(
        codex_home / "sessions" / "2026" / "unsupported.jsonl",
        [{"type": "session_meta", "payload": {"id": session_id, "model_provider": "codex-lb-ws"}}],
    )
    state_db = codex_home / "state_5.sqlite"
    _create_state_db(state_db, ["openai"])
    with closing(sqlite3.connect(state_db)) as conn:
        conn.execute("UPDATE threads SET id = ?", (session_id,))
        conn.commit()

    preview = preview_session_metadata_mismatches(codex_home=codex_home, active_provider="codex-lb")

    assert preview.mismatches == ()


def test_targeted_metadata_preview_rejects_unsupported_sqlite_provider_tag(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    session_id = "019f7249-90ee-74c0-a0e6-42117e80bc7f"
    _write_jsonl(
        codex_home / "sessions" / "2026" / "active.jsonl",
        [{"type": "session_meta", "payload": {"id": session_id, "model_provider": "codex-lb"}}],
    )
    for db_name, provider in (("state_5.sqlite", "openai"), ("state_6.sqlite", "codex-lb-ws")):
        state_db = codex_home / db_name
        _create_state_db(state_db, [provider])
        with closing(sqlite3.connect(state_db)) as conn:
            conn.execute("UPDATE threads SET id = ?", (session_id,))
            conn.commit()

    preview = preview_session_metadata_mismatches(codex_home=codex_home, active_provider="codex-lb")

    assert preview.mismatches == ()


def test_dry_run_reports_jsonl_and_sqlite_without_writing(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    session_file = codex_home / "sessions" / "2026" / "session.jsonl"
    state_db = codex_home / "state_5.sqlite"
    codex_home.mkdir()
    _write_jsonl(session_file, [{"model_provider": "openai", "id": "a"}])
    _create_state_db(state_db, ["openai", "codex-lb"])

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.methods_used == ("jsonl", "sqlite")
    assert result.jsonl_files_scanned == 1
    assert result.jsonl_files_matched == 1
    assert result.jsonl_files_updated == 0
    assert result.sqlite_dbs_scanned == 1
    assert result.sqlite_rows_matched == 1
    assert result.sqlite_rows_updated == 0
    assert result.backup_path is None
    assert json.loads(session_file.read_text(encoding="utf-8").splitlines()[0])["model_provider"] == "openai"
    assert _read_state_providers(state_db) == ["openai", "codex-lb"]
    assert not (codex_home / "backups").exists()


def test_discovery_stops_after_first_session_metadata_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / ".codex"
    session_file = codex_home / "sessions" / "2026" / "session.jsonl"
    _write_jsonl(
        session_file,
        [
            {"type": "session_meta", "payload": {"model_provider": "openai", "id": "meta"}},
            {"type": "turn_context", "payload": {"model_provider": "openai"}},
        ],
    )
    original_loads = json.loads

    def reject_turn_payload(value: str, *args, **kwargs):
        if '"type": "turn_context"' in value:
            raise AssertionError("discovery parsed beyond session metadata")
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(codex_sessions_retag.json, "loads", reject_turn_payload)

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
        dry_run=True,
    )

    assert result.jsonl_files_scanned == 1
    assert result.jsonl_files_matched == 1
    assert result.provider_counts_before == (ProviderCount("openai", 1),)


def test_discovery_does_not_scan_transcript_after_malformed_first_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / ".codex"
    session_file = codex_home / "sessions" / "2026" / "session.jsonl"
    session_file.parent.mkdir(parents=True)
    session_file.write_text(
        "{malformed-session-meta}\n"
        + json.dumps({"type": "turn_context", "payload": {"model_provider": "openai"}})
        + "\n",
        encoding="utf-8",
    )
    original_loads = json.loads

    def reject_turn_payload(value: str, *args, **kwargs):
        if '"type": "turn_context"' in value:
            raise AssertionError("discovery parsed transcript content after malformed metadata")
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(codex_sessions_retag.json, "loads", reject_turn_payload)

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
        dry_run=True,
    )

    assert result.jsonl_files_scanned == 1
    assert result.jsonl_files_matched == 0
    assert result.provider_counts_before == ()
    assert any("metadata was unavailable in 1 file" in line for line in result.logs)


def test_retag_accepts_and_preserves_utf8_bom_on_session_metadata(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    session_file = codex_home / "sessions" / "2026" / "session.jsonl"
    session_file.parent.mkdir(parents=True)
    metadata = json.dumps(
        {"type": "session_meta", "payload": {"model_provider": "openai", "id": "bom"}},
        separators=(",", ":"),
    ).encode("utf-8")
    session_file.write_bytes(b"\xef\xbb\xbf" + metadata + b"\ntranscript bytes are not parsed\n")

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
    )

    assert result.jsonl_files_matched == 1
    assert result.provider_counts_before == (ProviderCount("openai", 1),)
    rewritten = session_file.read_bytes()
    assert rewritten.startswith(b"\xef\xbb\xbf")
    assert (
        json.loads(rewritten.removeprefix(b"\xef\xbb\xbf").splitlines()[0])["payload"]["model_provider"] == "codex-lb"
    )


def test_jsonl_backup_prefers_hardlink_before_atomic_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / ".codex"
    session_file = codex_home / "sessions" / "2026" / "session.jsonl"
    original_bytes = (
        b'{"type":"session_meta","payload":{"model_provider":"openai","id":"meta"}}\r\n'
        b'{"type":"turn_context","payload":{"note":"preserve this byte-for-byte"}}\r\n'
    )
    session_file.parent.mkdir(parents=True)
    session_file.write_bytes(original_bytes)
    original_inode = session_file.stat().st_ino
    link_calls: list[tuple[Path, Path]] = []
    original_link = os.link

    def capture_link(source: Path, destination: Path) -> None:
        link_calls.append((Path(source), Path(destination)))
        original_link(source, destination)

    monkeypatch.setattr(codex_sessions_retag.os, "link", capture_link)

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
    )

    assert result.backup_path is not None
    backup_file = result.backup_path / "sessions" / "2026" / "session.jsonl"
    assert link_calls == [(session_file, backup_file)]
    assert backup_file.read_bytes() == original_bytes
    assert backup_file.stat().st_ino == original_inode
    assert session_file.stat().st_ino != original_inode
    assert session_file.read_bytes().splitlines(keepends=True)[1] == original_bytes.splitlines(keepends=True)[1]


def test_jsonl_backup_falls_back_to_flushed_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / ".codex"
    session_file = codex_home / "sessions" / "2026" / "session.jsonl"
    original_bytes = b'{"model_provider":"openai","id":"meta"}\ntrailing bytes stay unchanged\n'
    session_file.parent.mkdir(parents=True)
    session_file.write_bytes(original_bytes)

    def reject_link(_source: Path, _destination: Path) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(codex_sessions_retag.os, "link", reject_link)

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
    )

    assert result.backup_path is not None
    backup_file = result.backup_path / "sessions" / "2026" / "session.jsonl"
    assert backup_file.read_bytes() == original_bytes
    assert session_file.read_bytes().endswith(b"trailing bytes stay unchanged\n")


def test_sqlite_planning_queries_each_db_once_and_verifies_only_selected_db(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / ".codex"
    selected_db = codex_home / "state_5.sqlite"
    unselected_db = codex_home / "state_6.sqlite"
    codex_home.mkdir()
    _create_state_db(selected_db, ["openai", "codex-lb"])
    _create_state_db(unselected_db, ["codex-lb"])
    calls: dict[Path, int] = {}
    original_provider_counts = codex_sessions_retag._sqlite_provider_counts

    def capture_provider_counts(path: Path):
        calls[path] = calls.get(path, 0) + 1
        return original_provider_counts(path)

    monkeypatch.setattr(codex_sessions_retag, "_sqlite_provider_counts", capture_provider_counts)

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
    )

    assert result.sqlite_rows_matched == 1
    assert calls == {selected_db: 2, unselected_db: 1}


def test_retag_emits_structured_progress_for_each_work_phase(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    session_file = codex_home / "sessions" / "2026" / "session.jsonl"
    state_db = codex_home / "state_5.sqlite"
    codex_home.mkdir()
    _write_jsonl(session_file, [{"model_provider": "openai", "id": "meta"}])
    _create_state_db(state_db, ["openai"])
    output: list[str] = []

    retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
        progress_logger=output.append,
    )

    progress = [
        json.loads(line.removeprefix(codex_sessions_retag.PROGRESS_PREFIX))
        for line in output
        if line.startswith(codex_sessions_retag.PROGRESS_PREFIX)
    ]
    phases = {event["phase"] for event in progress}
    assert {"discovery", "backup", "jsonl_rewrite", "sqlite_update", "verification"} <= phases
    backup_identity_index = next(index for index, line in enumerate(output) if line.startswith("Created backup at "))
    first_rewrite_index = next(
        index
        for index, line in enumerate(output)
        if line.startswith(codex_sessions_retag.PROGRESS_PREFIX)
        and json.loads(line.removeprefix(codex_sessions_retag.PROGRESS_PREFIX))["phase"] == "jsonl_rewrite"
    )
    assert backup_identity_index < first_rewrite_index
    for phase in phases:
        final = [event for event in progress if event["phase"] == phase][-1]
        assert final["completed"] == final["total"]


def test_retag_emits_intermediate_progress_while_updating_large_sqlite_database(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    state_db = codex_home / "state_5.sqlite"
    codex_home.mkdir()
    _create_state_db(state_db, ["openai"] * 2_501)
    output: list[str] = []

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
        progress_logger=output.append,
    )

    progress = [
        json.loads(line.removeprefix(codex_sessions_retag.PROGRESS_PREFIX))
        for line in output
        if line.startswith(codex_sessions_retag.PROGRESS_PREFIX)
    ]
    sqlite_progress = [event for event in progress if event["phase"] == "sqlite_update"]
    assert result.sqlite_rows_updated == 2_501
    assert any(0 < event["completed"] < event["total"] for event in sqlite_progress)
    assert sqlite_progress[-1]["completed"] == sqlite_progress[-1]["total"] == 2_501


def test_retag_updates_jsonl_and_sqlite_with_backup(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    session_file = codex_home / "sessions" / "2026" / "session.jsonl"
    state_db = codex_home / "state_5.sqlite"
    codex_home.mkdir()
    _write_jsonl(
        session_file,
        [
            {"type": "session_meta", "payload": {"model_provider": "openai", "id": "a"}},
            {"type": "turn_context", "payload": {"model_provider": "codex-lb", "id": "b"}},
        ],
    )
    _create_state_db(state_db, ["openai", "openai", "codex-lb"])

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
    )

    records = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines()]
    assert records[0]["payload"]["model_provider"] == "codex-lb"
    assert records[1]["payload"]["model_provider"] == "codex-lb"
    assert _read_state_providers(state_db) == ["codex-lb", "codex-lb", "codex-lb"]
    assert result.methods_used == ("jsonl", "sqlite")
    assert result.jsonl_files_updated == 1
    assert result.sqlite_rows_updated == 2
    assert result.backup_path is not None
    assert (result.backup_path / "state_5.sqlite").is_file()
    assert (result.backup_path / "sessions" / "2026" / "session.jsonl").is_file()
    assert ProviderCount("openai", 3) in result.provider_counts_before
    assert ProviderCount("codex-lb", 4) in result.provider_counts_after


def test_retag_updates_nested_session_meta_provider(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    session_file = codex_home / "sessions" / "2026" / "session.jsonl"
    _write_jsonl(
        session_file,
        [
            {"type": "session_meta", "payload": {"model_provider": "openai", "id": "meta"}},
            {"type": "turn_context", "payload": {"model_provider": "openai"}},
        ],
    )

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
    )

    records = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines()]
    assert records[0]["payload"]["model_provider"] == "codex-lb"
    assert records[1]["payload"]["model_provider"] == "openai"
    assert result.jsonl_files_matched == 1
    assert ProviderCount("openai", 1) in result.provider_counts_before
    assert ProviderCount("codex-lb", 1) in result.provider_counts_after


def test_retag_rewrites_only_planned_metadata_line(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    session_file = codex_home / "sessions" / "2026" / "session.jsonl"
    session_file.parent.mkdir(parents=True)
    tail = b"{not-json}\n\xff\x00transcript bytes\r\n"
    session_file.write_bytes(b'{"model_provider": "openai", "id": "a"}\n' + tail)

    retag_codex_sessions(codex_home=codex_home, source_provider="openai", target_provider="codex-lb")

    rewritten = session_file.read_bytes()
    assert rewritten.startswith(b'{"model_provider":"codex-lb","id":"a"}\n')
    assert rewritten.endswith(tail)


def test_retag_surfaces_unreadable_jsonl_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    session_file = codex_home / "sessions" / "2026" / "session.jsonl"
    _write_jsonl(session_file, [{"model_provider": "openai", "id": "a"}])

    original_open = Path.open

    def deny_session_read(
        self: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ):
        if self == session_file and "r" in mode:
            raise PermissionError(f"cannot read {self}")
        return original_open(
            self,
            mode=mode,
            buffering=buffering,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "open", deny_session_read)

    with pytest.raises(PermissionError, match="cannot read"):
        retag_codex_sessions(
            codex_home=codex_home,
            source_provider="openai",
            target_provider="codex-lb",
        )

    assert not (codex_home / "backups").exists()


def test_retag_supports_jsonl_only_storage(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    session_file = codex_home / "sessions" / "session.jsonl"
    _write_jsonl(session_file, [{"model_provider": "openai"}])

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
    )

    assert result.methods_used == ("jsonl",)
    assert result.sqlite_dbs_scanned == 0
    assert json.loads(session_file.read_text(encoding="utf-8"))["model_provider"] == "codex-lb"


def test_retag_uses_copy_fallback_when_live_sqlite_cannot_open(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    state_db = codex_home / "state_5.sqlite"
    codex_home.mkdir()
    _create_state_db(state_db, ["openai"])

    original_connect = codex_sessions_retag._connect_sqlite

    def flaky_connect(path: Path, *, read_only: bool = False, immutable: bool = False) -> sqlite3.Connection:
        if path == state_db and not read_only:
            raise sqlite3.OperationalError("unable to open database file")
        return original_connect(path, read_only=read_only, immutable=immutable)

    monkeypatch.setattr(codex_sessions_retag, "_connect_sqlite", flaky_connect)

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
    )

    assert result.sqlite_rows_updated == 1
    assert _read_state_providers(state_db) == ["codex-lb"]


def test_read_only_sqlite_count_uses_non_immutable_uri(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state_db = tmp_path / "state_5.sqlite"
    _create_state_db(state_db, ["openai"])
    original_connect = codex_sessions_retag.sqlite3.connect
    calls: list[tuple[str, bool]] = []

    def capture_connect(target: str, *, timeout: int, uri: bool) -> sqlite3.Connection:
        calls.append((target, uri))
        return original_connect(target, timeout=timeout, uri=uri)

    monkeypatch.setattr(codex_sessions_retag.sqlite3, "connect", capture_connect)

    assert codex_sessions_retag._sqlite_count_provider_rows(state_db, "openai") == 1
    assert calls[0][1] is True
    assert calls[0][0].startswith("file:")
    assert "mode=ro" in calls[0][0]
    assert "immutable=1" not in calls[0][0]


def test_sqlite_provider_count_closes_connection_explicitly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_db = tmp_path / "state_5.sqlite"
    _create_state_db(state_db, ["openai"])
    original_connect = codex_sessions_retag._connect_sqlite

    class TrackingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection
            self.closed = False

        def __getattr__(self, name: str):
            return getattr(self.connection, name)

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def close(self) -> None:
            self.closed = True
            self.connection.close()

    tracked: list[TrackingConnection] = []

    def tracking_connect(path: Path, *, read_only: bool = False, immutable: bool = False):
        connection = TrackingConnection(original_connect(path, read_only=read_only, immutable=immutable))
        tracked.append(connection)
        return connection

    monkeypatch.setattr(codex_sessions_retag, "_connect_sqlite", tracking_connect)

    assert codex_sessions_retag._sqlite_provider_counts(state_db) == (ProviderCount("openai", 1),)
    assert tracked
    assert all(connection.closed for connection in tracked)


def test_retag_skips_legacy_sqlite_without_model_provider_column(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    state_db = codex_home / "state_4.sqlite"
    session_file = codex_home / "sessions" / "2026" / "session.jsonl"
    codex_home.mkdir()
    _write_jsonl(session_file, [{"model_provider": "openai", "id": "a"}])
    with closing(sqlite3.connect(state_db)) as conn:
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO threads (id) VALUES ('thread-legacy')")
        conn.commit()

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
    )

    assert result.sqlite_dbs_scanned == 1
    assert result.sqlite_rows_matched == 0
    assert result.jsonl_files_updated == 1


def test_retag_ignores_noncanonical_state_database_backups(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    _create_state_db(codex_home / "state_5.sqlite", ["openai"])
    _create_state_db(codex_home / "state_5.sqlite.xml-cwd-backup.sqlite", ["openai"])

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
        dry_run=True,
    )

    assert result.sqlite_dbs_scanned == 1
    assert result.sqlite_dbs_matched == 1
    assert result.sqlite_rows_matched == 1


def test_sqlite_uri_path_normalizes_windows_separators() -> None:
    quoted = codex_sessions_retag._quote_sqlite_uri_path(r"C:\Users\nicef\.codex\state_5.sqlite")

    assert quoted == "C:/Users/nicef/.codex/state_5.sqlite"
    assert "%5C" not in quoted


def test_sqlite_backup_consolidates_wal_rows(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    state_db = codex_home / "state_5.sqlite"
    codex_home.mkdir()
    with closing(sqlite3.connect(state_db)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT)")
        conn.execute("INSERT INTO threads (id, model_provider) VALUES ('thread-1', 'openai')")
        conn.commit()
        assert Path(f"{state_db}-wal").stat().st_size > 0

        backup_dir = codex_sessions_retag._create_backup(codex_home, (), (state_db,))

    backup_db = backup_dir / "state_5.sqlite"
    assert _read_state_providers(backup_db) == ["openai"]
    assert not Path(f"{backup_db}-wal").exists()


def test_sqlite_copy_fallback_uses_atomic_consolidated_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / ".codex"
    state_db = codex_home / "state_5.sqlite"
    codex_home.mkdir()
    with closing(sqlite3.connect(state_db)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT)")
        conn.execute("INSERT INTO threads (id, model_provider) VALUES ('thread-1', 'openai')")
        conn.commit()
        assert Path(f"{state_db}-wal").stat().st_size > 0

        temp_path = codex_sessions_retag._copy_sqlite_to_temp(state_db)

    try:
        assert _read_state_providers(temp_path) == ["openai"]
        codex_sessions_retag._update_sqlite_provider_in_place(
            temp_path,
            "openai",
            "codex-lb",
            expected_rows=1,
        )
        Path(f"{state_db}-wal").write_bytes(b"stale wal")
        Path(f"{state_db}-shm").write_bytes(b"stale shm")
        replacements: list[tuple[Path, Path]] = []
        original_replace = codex_sessions_retag.os.replace

        def capture_replace(source: Path, destination: Path) -> None:
            replacements.append((Path(source), Path(destination)))
            original_replace(source, destination)

        def reject_non_atomic_copy(*_args, **_kwargs):
            raise AssertionError("SQLite fallback copied directly over the live database")

        monkeypatch.setattr(codex_sessions_retag.os, "replace", capture_replace)
        monkeypatch.setattr(codex_sessions_retag.shutil, "copy2", reject_non_atomic_copy)
        codex_sessions_retag._replace_sqlite_db(temp_path, state_db)

        assert _read_state_providers(state_db) == ["codex-lb"]
        assert replacements == [(temp_path, state_db)]
        assert not Path(f"{state_db}-wal").exists()
        assert not Path(f"{state_db}-shm").exists()
    finally:
        temp_path.unlink(missing_ok=True)


def test_retag_stays_inside_configured_codex_home(tmp_path: Path) -> None:
    codex_home = tmp_path / "mounted" / ".codex"
    other_home = tmp_path / "host" / ".codex"
    codex_home.mkdir(parents=True)
    other_home.mkdir(parents=True)
    mounted_db = codex_home / "state_5.sqlite"
    host_db = other_home / "state_5.sqlite"
    _create_state_db(mounted_db, ["openai"])
    _create_state_db(host_db, ["openai"])

    retag_codex_sessions(codex_home=codex_home, source_provider="openai", target_provider="codex-lb")

    assert _read_state_providers(mounted_db) == ["codex-lb"]
    assert _read_state_providers(host_db) == ["openai"]


def test_default_codex_home_prefers_codex_home_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    explicit_home = tmp_path / "codex-home"

    monkeypatch.setenv("CODEX_HOME", str(explicit_home))

    assert codex_sessions_retag.default_codex_home() == explicit_home


def test_wsl_codex_home_detects_only_current_windows_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    current_profile = tmp_path / "Users" / "current"
    other_profile = tmp_path / "Users" / "other"
    (current_profile / ".codex").mkdir(parents=True)
    (other_profile / ".codex").mkdir(parents=True)

    monkeypatch.setenv("USERPROFILE", "C:\\Users\\current")
    monkeypatch.setattr(
        codex_sessions_retag,
        "_wsl_path_from_windows_userprofile",
        lambda userprofile: current_profile,
    )

    assert codex_sessions_retag._discover_wsl_windows_codex_home() == current_profile / ".codex"


def test_wsl_codex_home_does_not_scan_other_windows_profiles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    other_profile = tmp_path / "Users" / "other"
    (other_profile / ".codex").mkdir(parents=True)

    monkeypatch.delenv("USERPROFILE", raising=False)

    assert codex_sessions_retag._discover_wsl_windows_codex_home() is None


def test_windows_userprofile_maps_to_wsl_mount_path() -> None:
    assert codex_sessions_retag._wsl_path_from_windows_userprofile("C:\\Users\\nicef") == Path("/mnt/c/Users/nicef")


def test_container_cgroup_detection_is_scoped_to_retag_module(tmp_path: Path) -> None:
    cgroup = tmp_path / "cgroup"
    cgroup.write_text("0::/docker/container-id\n", encoding="utf-8")

    assert codex_sessions_retag._cgroup_mentions_container(cgroup) is True
