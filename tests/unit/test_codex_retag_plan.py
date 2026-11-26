from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app import cli

pytestmark = pytest.mark.unit


def _run(home: Path, *flags: str) -> None:
    cli.main(
        [
            "codex-sessions",
            "retag",
            "--from",
            "openai",
            "--to",
            "codex-lb",
            "--codex-home",
            str(home),
            *flags,
        ]
    )


def _session(home: Path, name: str, provider: str = "openai") -> Path:
    path = home / "sessions" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        json.dumps({"type": "session_meta", "payload": {"id": name, "model_provider": provider}}).encode()
        + b"\n"
        + b"\xff" * 100_000
    )
    return path


def test_cli_plan_reads_metadata_only_and_groups_each_database_once(tmp_path, monkeypatch, capsys):
    path = _session(tmp_path, "selected")
    original = path.read_bytes()
    db = tmp_path / "state_5.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT)")
        conn.executemany("INSERT INTO threads VALUES (?, ?)", [("one", "openai"), ("two", "codex-lb")])
    statements = []
    connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    _run(tmp_path, "--dry-run")
    output = capsys.readouterr().out
    assert "Would update JSONL files: 1" in output
    assert "Would update SQLite rows: 1" in output
    assert [sql for sql in statements if "FROM threads" in sql] == [
        "SELECT model_provider, COUNT(*) FROM threads GROUP BY model_provider"
    ]
    assert path.read_bytes() == original
    assert not (tmp_path / "backups").exists()


def test_cli_write_keeps_transcript_and_hardlink_backup(tmp_path, capsys):
    path = _session(tmp_path, "selected")
    untouched = _session(tmp_path, "untouched", "codex-lb")
    original = path.read_bytes()
    untouched_original = untouched.read_bytes()
    original_inode = path.stat().st_ino
    config = tmp_path / "config.toml"
    config.write_text('model_provider = "openai"\n')
    _run(tmp_path, "--yes")
    backup = next((tmp_path / "backups" / "provider-retag").glob("*/sessions/selected.jsonl"))
    assert backup.read_bytes() == original
    assert backup.stat().st_ino == original_inode
    assert path.stat().st_ino != original_inode
    header, tail = path.read_bytes().split(b"\n", 1)
    assert json.loads(header)["payload"]["model_provider"] == "codex-lb"
    assert tail == original.split(b"\n", 1)[1]
    assert untouched.read_bytes() == untouched_original
    assert config.read_text() == 'model_provider = "openai"\n'
    assert "Updated JSONL files: 1" in capsys.readouterr().out


def test_cli_json_progress_reports_all_write_phases(tmp_path, capsys):
    _session(tmp_path, "selected")
    _run(tmp_path, "--yes", "--progress-json")
    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.err.splitlines()]
    for phase in ("discovery", "backup", "rewrite", "verification"):
        phase_events = [event for event in events if event["phase"] == phase]
        assert phase_events[0]["completed"] == 0
        assert phase_events[-1]["completed"] == phase_events[-1]["total"] == 1
    assert events[-1]["phase"] == "complete"
    assert "Updated JSONL files: 1" in captured.out


def test_cli_failure_reports_retained_backup(tmp_path, monkeypatch, capsys):
    path = _session(tmp_path, "selected")
    original = path.read_bytes()
    replace = Path.replace

    def deny_replacement(self, target):
        if Path(target) == path:
            raise PermissionError("fixture replacement denied")
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", deny_replacement)
    with pytest.raises(SystemExit, match="Backup retained at"):
        _run(tmp_path, "--yes", "--progress-json")
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert not any(event["phase"] == "complete" for event in events)
    assert path.read_bytes() == original
    backup = next((tmp_path / "backups" / "provider-retag").glob("*/sessions/selected.jsonl"))
    assert backup.read_bytes() == original


def test_cli_discovery_bytes_do_not_grow_with_transcript(tmp_path, monkeypatch, capsys):
    paths = [_session(tmp_path, "small"), _session(tmp_path, "large")]
    with paths[1].open("ab") as handle:
        handle.write(b"x" * 2_000_000)
    byte_counts = {path: 0 for path in paths}
    open_path = Path.open

    class MeteredRead:
        def __init__(self, handle, path):
            self.handle = handle
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def read(self, size=-1):
            data = self.handle.read(size)
            byte_counts[self.path] += len(data)
            return data

    def metered_open(self, *args, **kwargs):
        handle = open_path(self, *args, **kwargs)
        return MeteredRead(handle, self) if self in byte_counts else handle

    monkeypatch.setattr(Path, "open", metered_open)
    _run(tmp_path, "--dry-run")
    assert byte_counts[paths[0]] == byte_counts[paths[1]] == 65536
    assert "Would update JSONL files: 2" in capsys.readouterr().out


def test_cli_hardlink_failure_uses_independent_copy(tmp_path, monkeypatch):
    import os

    path = _session(tmp_path, "selected")
    original = path.read_bytes()
    inode = path.stat().st_ino

    def denied_link(*args, **kwargs):
        raise OSError("fixture does not support hard links")

    monkeypatch.setattr(os, "link", denied_link)
    _run(tmp_path, "--yes")
    backup = next((tmp_path / "backups" / "provider-retag").glob("*/sessions/selected.jsonl"))
    assert backup.read_bytes() == original
    assert backup.stat().st_ino != inode


def test_cli_failed_backup_never_mutates_sessions(tmp_path, monkeypatch):
    import os
    import shutil

    path = _session(tmp_path, "selected")
    original = path.read_bytes()

    def denied(*args, **kwargs):
        raise PermissionError("fixture backup denied")

    monkeypatch.setattr(os, "link", denied)
    monkeypatch.setattr(shutil, "copy2", denied)
    with pytest.raises(SystemExit, match="Backup failed before mutation"):
        _run(tmp_path, "--yes")
    assert path.read_bytes() == original


def test_cli_rejects_oversized_initial_metadata_before_backup(tmp_path):
    path = _session(tmp_path, "selected")
    original = b'{"model_provider":"openai","long":"' + b"x" * 70_000 + b'"}\n'
    path.write_bytes(original)
    with pytest.raises(SystemExit, match="metadata exceeds 65536 bytes"):
        _run(tmp_path, "--yes")
    assert path.read_bytes() == original
    assert not (tmp_path / "backups").exists()


def test_cli_verifies_database_changes_instead_of_assuming_success(tmp_path):
    db = tmp_path / "state_5.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT)")
        conn.execute("INSERT INTO threads VALUES ('one', 'openai')")
        conn.execute("CREATE TRIGGER ignore_update BEFORE UPDATE ON threads BEGIN SELECT RAISE(IGNORE); END")
    with pytest.raises(SystemExit, match="Retag verification failed.*Backup retained at"):
        _run(tmp_path, "--yes")


def test_cli_verifies_only_planned_files_and_databases(tmp_path, monkeypatch):
    _session(tmp_path, "selected")
    unrelated = _session(tmp_path, "unrelated", "codex-lb")
    for name, provider in (("state_5.sqlite", "openai"), ("state_6.sqlite", "codex-lb")):
        with sqlite3.connect(tmp_path / name) as conn:
            conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT)")
            conn.execute("INSERT INTO threads VALUES ('one', ?)", (provider,))
    unrelated_reads = []
    grouped_queries = []
    open_path = Path.open
    connect = sqlite3.connect

    def capture_open(self, *args, **kwargs):
        if self == unrelated:
            unrelated_reads.append(self)
        return open_path(self, *args, **kwargs)

    def capture_connect(target, *args, **kwargs):
        conn = connect(target, *args, **kwargs)
        conn.set_trace_callback(
            lambda sql: grouped_queries.append(str(target)) if "GROUP BY model_provider" in sql else None
        )
        return conn

    monkeypatch.setattr(Path, "open", capture_open)
    monkeypatch.setattr(sqlite3, "connect", capture_connect)
    _run(tmp_path, "--yes")
    assert len(unrelated_reads) == 1
    assert sum("state_5.sqlite" in query for query in grouped_queries) == 2
    assert sum("state_6.sqlite" in query for query in grouped_queries) == 1


def test_cli_legacy_metadata_with_large_transcript_tail(tmp_path):
    path = tmp_path / "sessions" / "legacy.jsonl"
    path.parent.mkdir()
    transcript = json.dumps({"type": "turn_context", "text": "x" * 100_000}).encode() + b"\n"
    path.write_bytes(b'{"model_provider":"openai","id":"legacy"}\n' + transcript)
    _run(tmp_path, "--dry-run")
    _run(tmp_path, "--yes")
    header, tail = path.read_bytes().split(b"\n", 1)
    assert json.loads(header)["model_provider"] == "codex-lb"
    assert tail == transcript


@pytest.mark.parametrize("phase", ["discovery", "backup", "rewrite", "verification"])
def test_cli_closed_progress_pipe_does_not_interrupt_retag(tmp_path, monkeypatch, capsys, phase):
    path = _session(tmp_path, "selected")
    original = path.read_bytes()

    class ClosingProgressReader:
        failed = False

        def write(self, text):
            if self.failed:
                pytest.fail("Progress was written after the reader closed")
            if text.startswith("{") and json.loads(text)["phase"] == phase:
                self.failed = True
                raise BrokenPipeError("progress reader closed")
            return len(text)

        def flush(self):
            pass

    reader = ClosingProgressReader()
    monkeypatch.setattr("sys.stderr", reader)
    _run(tmp_path, "--yes", "--progress-json")
    assert reader.failed
    assert "Updated JSONL files: 1" in capsys.readouterr().out
    header, tail = path.read_bytes().split(b"\n", 1)
    assert json.loads(header)["payload"]["model_provider"] == "codex-lb"
    assert tail == original.split(b"\n", 1)[1]
    backup = next((tmp_path / "backups" / "provider-retag").glob("*/sessions/selected.jsonl"))
    assert backup.read_bytes() == original


def test_cli_closed_progress_pipe_exits_successfully(tmp_path):
    import os
    import subprocess
    import sys

    path = _session(tmp_path, "selected")
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        result = subprocess.run(
            [
                str(Path(sys.executable).with_name("codex-lb")),
                "codex-sessions",
                "retag",
                "--from",
                "openai",
                "--to",
                "codex-lb",
                "--codex-home",
                str(tmp_path),
                "--yes",
                "--progress-json",
            ],
            stdout=subprocess.PIPE,
            stderr=write_fd,
            text=True,
            check=False,
        )
    finally:
        os.close(write_fd)
    assert result.returncode == 0
    assert "Updated JSONL files: 1" in result.stdout
    assert json.loads(path.read_bytes().split(b"\n", 1)[0])["payload"]["model_provider"] == "codex-lb"
