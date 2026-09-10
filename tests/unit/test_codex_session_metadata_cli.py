from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app import cli

pytestmark = pytest.mark.unit


def make_home(home: Path) -> None:
    sessions = home / "sessions"
    sessions.mkdir()
    for session_id, provider in (("split", "openai"), ("unrelated", "openai")):
        record = {"type": "session_meta", "payload": {"id": session_id, "model_provider": provider}}
        (sessions / f"{session_id}.jsonl").write_bytes(json.dumps(record).encode() + b"\nopaque transcript\r\n")
    with sqlite3.connect(home / "state_5.sqlite") as conn:
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT, title TEXT)")
        conn.executemany(
            "INSERT INTO threads VALUES (?, ?, ?)", [("split", "codex-lb", "Keep"), ("unrelated", "openai", "Keep")]
        )
    (home / "config.toml").write_text('model_provider = "openai"\n')


def snapshot(home: Path) -> dict[str, bytes]:
    return {str(path.relative_to(home)): path.read_bytes() for path in home.rglob("*") if path.is_file()}


def test_preview_reports_only_mismatch_without_writing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_home(tmp_path)
    before = snapshot(tmp_path)
    cli.main(
        ["codex-sessions", "metadata-mismatches", "--provider", "codex-lb", "--codex-home", str(tmp_path), "--json"]
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["mismatches"] == [{"session_id": "split", "providers": ["codex-lb", "openai"]}]
    assert snapshot(tmp_path) == before
    assert json.loads(captured.err.splitlines()[-1])["phase"] == "discovery"


def test_confirmed_repair_preserves_other_sessions_and_backup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_home(tmp_path)
    before = snapshot(tmp_path)
    cli.main(
        [
            "codex-sessions",
            "repair-metadata",
            "--provider",
            "codex-lb",
            "--session-id",
            "split",
            "--yes",
            "--codex-home",
            str(tmp_path),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    backup = Path(result["backup_path"])
    assert result["session_ids"] == ["split"]
    assert (backup / "sessions/split.jsonl").read_bytes() == before["sessions/split.jsonl"]
    changed = (tmp_path / "sessions/split.jsonl").read_bytes()
    assert json.loads(changed.splitlines()[0])["payload"]["model_provider"] == "codex-lb"
    assert changed.split(b"\n", 1)[1] == b"opaque transcript\r\n"
    assert (tmp_path / "sessions/unrelated.jsonl").read_bytes() == before["sessions/unrelated.jsonl"]
    assert (tmp_path / "config.toml").read_bytes() == before["config.toml"]
    with sqlite3.connect(tmp_path / "state_5.sqlite") as conn:
        assert conn.execute("SELECT * FROM threads ORDER BY id").fetchall() == [
            ("split", "codex-lb", "Keep"),
            ("unrelated", "openai", "Keep"),
        ]
    phases = {json.loads(line)["phase"] for line in captured.err.splitlines()}
    assert {"discovery", "backup", "rewrite", "sqlite", "verification"} <= phases


def repair_args(home: Path, *extra: str) -> list[str]:
    return ["codex-sessions", "repair-metadata", "--codex-home", str(home), "--json", *extra]


@pytest.mark.parametrize(
    "extra",
    [
        ["--provider", "codex-lb", "--session-id", "split"],
        ["--provider", "codex-lb", "--session-id", "missing", "--yes"],
        ["--provider", "other", "--session-id", "split", "--yes"],
        ["--provider", "codex-lb", "--yes"],
    ],
)
def test_invalid_repair_does_not_write(tmp_path: Path, extra: list[str]) -> None:
    make_home(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(repair_args(tmp_path, *extra))
    assert snapshot(tmp_path) == before


def test_repair_rejects_unsupported_observed_tag(tmp_path: Path) -> None:
    make_home(tmp_path)
    with sqlite3.connect(tmp_path / "state_5.sqlite") as conn:
        conn.execute("UPDATE threads SET model_provider = 'custom' WHERE id = 'split'")
    before = snapshot(tmp_path)
    with pytest.raises(SystemExit, match="unsupported"):
        cli.main(repair_args(tmp_path, "--provider", "codex-lb", "--session-id", "split", "--yes"))
    assert snapshot(tmp_path) == before


def test_sqlite_repair_keeps_snapshot_and_unrelated_rows(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_home(tmp_path)
    before = snapshot(tmp_path)
    cli.main(repair_args(tmp_path, "--provider", "openai", "--session-id", "split", "--yes"))
    backup = Path(json.loads(capsys.readouterr().out)["backup_path"])
    with sqlite3.connect(backup / "state_5.sqlite") as conn:
        assert conn.execute("SELECT model_provider FROM threads WHERE id='split'").fetchone() == ("codex-lb",)
    with sqlite3.connect(tmp_path / "state_5.sqlite") as conn:
        assert conn.execute("SELECT * FROM threads ORDER BY id").fetchall() == [
            ("split", "openai", "Keep"),
            ("unrelated", "openai", "Keep"),
        ]
    assert (tmp_path / "sessions/split.jsonl").read_bytes() == before["sessions/split.jsonl"]


def test_copy_fallback_preserves_exact_jsonl_backup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    make_home(tmp_path)
    before = (tmp_path / "sessions/split.jsonl").read_bytes()

    def unavailable(*args: object, **kwargs: object) -> None:
        raise OSError("Hard links unavailable")

    monkeypatch.setattr(os, "link", unavailable)
    cli.main(repair_args(tmp_path, "--provider", "codex-lb", "--session-id", "split", "--yes"))
    backup = Path(json.loads(capsys.readouterr().out)["backup_path"])
    assert (backup / "sessions/split.jsonl").read_bytes() == before


def test_sqlite_write_failure_retains_recovery_location(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_home(tmp_path)
    with sqlite3.connect(tmp_path / "state_5.sqlite") as conn:
        conn.execute(
            "CREATE TRIGGER refuse BEFORE UPDATE ON threads BEGIN SELECT RAISE(ABORT, 'read-only fixture'); END"
        )
    before = snapshot(tmp_path)
    with pytest.raises(SystemExit, match="retained backup at"):
        cli.main(repair_args(tmp_path, "--provider", "openai", "--session-id", "split", "--yes"))
    assert capsys.readouterr().out == ""
    backups = list((tmp_path / "backups/session-metadata-repair").glob("repair-*/state_5.sqlite"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("SELECT model_provider FROM threads WHERE id='split'").fetchone() == ("codex-lb",)
    for path, content in before.items():
        assert (tmp_path / path).read_bytes() == content


def test_repair_rejects_null_selected_provider(tmp_path: Path) -> None:
    make_home(tmp_path)
    with sqlite3.connect(tmp_path / "state_5.sqlite") as conn:
        conn.execute("UPDATE threads SET model_provider = NULL WHERE id='split'")
    before = snapshot(tmp_path)
    with pytest.raises(SystemExit, match="provider"):
        cli.main(repair_args(tmp_path, "--provider", "codex-lb", "--session-id", "split", "--yes"))
    assert snapshot(tmp_path) == before


def test_duplicate_json_keys_fail_before_mutation(tmp_path: Path) -> None:
    make_home(tmp_path)
    (tmp_path / "sessions/split.jsonl").write_text(
        '{"type":"session_meta","payload":{"id":"split","model_provider":"openai","keep":1,"keep":2}}\n'
    )
    before = snapshot(tmp_path)
    with pytest.raises(SystemExit, match="Duplicate"):
        cli.main(repair_args(tmp_path, "--provider", "codex-lb", "--session-id", "split", "--yes"))
    assert snapshot(tmp_path) == before


def test_symlink_backup_directory_is_rejected(tmp_path: Path) -> None:
    home, outside = tmp_path / "home", tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    make_home(home)
    (home / "backups").symlink_to(outside, target_is_directory=True)
    before = snapshot(home)
    with pytest.raises(SystemExit, match="backup"):
        cli.main(repair_args(home, "--provider", "codex-lb", "--session-id", "split", "--yes"))
    assert snapshot(home) == before
    assert list(outside.iterdir()) == []


def test_preview_reports_consistent_unsupported_tags(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_home(tmp_path)
    for path in (tmp_path / "sessions").glob("*.jsonl"):
        path.write_bytes(path.read_bytes().replace(b"openai", b"custom"))
    with sqlite3.connect(tmp_path / "state_5.sqlite") as conn:
        conn.execute("UPDATE threads SET model_provider='custom'")
    cli.main(
        ["codex-sessions", "metadata-mismatches", "--provider", "codex-lb", "--codex-home", str(tmp_path), "--json"]
    )
    result = json.loads(capsys.readouterr().out)
    assert result["unsupported_sessions"] == ["split", "unrelated"]


@pytest.mark.parametrize("replace_database", [False, True])
def test_stale_target_is_not_overwritten_after_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_database: bool
) -> None:
    import builtins

    make_home(tmp_path)
    real_print = builtins.print
    changed = False
    db_path = tmp_path / "state_5.sqlite"
    jsonl_path = tmp_path / "sessions/split.jsonl"

    def observe_progress(*args: object, **kwargs: Any) -> None:
        nonlocal changed
        real_print(*args, **kwargs)
        if changed or not args or not isinstance(args[0], str):
            return
        try:
            event = json.loads(args[0])
        except ValueError:
            return
        if event.get("phase") != "backup" or event.get("completed") != 1:
            return
        changed = True
        if replace_database:
            replacement = tmp_path / "replacement.sqlite"
            with sqlite3.connect(replacement) as conn:
                conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT, title TEXT)")
                conn.execute("INSERT INTO threads VALUES ('split', 'codex-lb', 'Replacement')")
            replacement.replace(db_path)
        else:
            with jsonl_path.open("ab") as handle:
                handle.write(b"new external transcript\n")

    monkeypatch.setattr(builtins, "print", observe_progress)
    with pytest.raises(SystemExit, match="changed"):
        cli.main(
            repair_args(
                tmp_path, "--provider", "openai" if replace_database else "codex-lb", "--session-id", "split", "--yes"
            )
        )
    assert changed
    if replace_database:
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT * FROM threads").fetchall() == [("split", "codex-lb", "Replacement")]
    else:
        assert json.loads(jsonl_path.read_bytes().splitlines()[0])["payload"]["model_provider"] == "openai"
        assert jsonl_path.read_bytes().endswith(b"new external transcript\n")


@pytest.mark.parametrize("force_copy", [False, True])
def test_archived_legacy_header_and_large_opaque_body(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, force_copy: bool
) -> None:
    import os

    if force_copy:

        def unavailable(*args: object, **kwargs: object) -> None:
            raise OSError("Hard links unavailable")

        monkeypatch.setattr(os, "link", unavailable)

    make_home(tmp_path)
    archive = tmp_path / "archived_sessions"
    archive.mkdir()
    body = b"\xffnot json\r\n" * 120000
    path = archive / "legacy.jsonl"
    original = b'{"id":"archived","model_provider":"openai","keep":42}\r\n' + body
    path.write_bytes(original)
    inode = path.stat().st_ino
    probe = tmp_path / "hard-link-probe"
    try:
        os.link(path, probe)
    except OSError:
        supports_hard_links = False
    else:
        supports_hard_links = True
        probe.unlink()
    cli.main(repair_args(tmp_path, "--provider", "codex-lb", "--session-id", "archived", "--yes"))
    captured = capsys.readouterr()
    backup = Path(json.loads(captured.out)["backup_path"])
    assert (backup / "archived_sessions/legacy.jsonl").read_bytes() == original
    if supports_hard_links:
        assert (backup / "archived_sessions/legacy.jsonl").stat().st_ino == inode
    assert path.read_bytes().split(b"\n", 1)[1] == body
    assert json.loads(path.read_bytes().split(b"\n", 1)[0]) == {
        "id": "archived",
        "model_provider": "codex-lb",
        "keep": 42,
    }
    assert len([line for line in captured.err.splitlines() if json.loads(line)["phase"] == "rewrite"]) >= 3


def test_oversized_header_is_rejected_before_backup(tmp_path: Path) -> None:
    make_home(tmp_path)
    (tmp_path / "sessions/split.jsonl").write_bytes(b" " * (1024 * 1024 + 1))
    before = snapshot(tmp_path)
    with pytest.raises(SystemExit, match="1 MiB"):
        cli.main(repair_args(tmp_path, "--provider", "codex-lb", "--session-id", "split", "--yes"))
    assert snapshot(tmp_path) == before


def test_noop_repair_verifies_without_backup(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_home(tmp_path)
    before = snapshot(tmp_path)
    cli.main(repair_args(tmp_path, "--provider", "openai", "--session-id", "unrelated", "--yes"))
    captured = capsys.readouterr()
    assert json.loads(captured.out)["backup_path"] is None
    assert json.loads(captured.err.splitlines()[-1])["phase"] == "verification"
    assert snapshot(tmp_path) == before
