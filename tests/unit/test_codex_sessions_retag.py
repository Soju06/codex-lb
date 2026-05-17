from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app import codex_sessions_retag
from app.codex_sessions_retag import ProviderCount, retag_codex_sessions

pytestmark = pytest.mark.unit


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def _create_state_db(path: Path, providers: list[str]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT)")
        conn.executemany(
            "INSERT INTO threads (id, model_provider) VALUES (?, ?)",
            [(f"thread-{index}", provider) for index, provider in enumerate(providers)],
        )


def _read_state_providers(path: Path) -> list[str]:
    with sqlite3.connect(path) as conn:
        return [row[0] for row in conn.execute("SELECT model_provider FROM threads ORDER BY id").fetchall()]


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


def test_retag_updates_jsonl_and_sqlite_with_backup(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    session_file = codex_home / "sessions" / "2026" / "session.jsonl"
    state_db = codex_home / "state_5.sqlite"
    codex_home.mkdir()
    _write_jsonl(
        session_file,
        [
            {"model_provider": "openai", "id": "a"},
            {"model_provider": "codex-lb", "id": "b"},
        ],
    )
    _create_state_db(state_db, ["openai", "openai", "codex-lb"])

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
    )

    records = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines()]
    assert [record["model_provider"] for record in records] == ["codex-lb", "codex-lb"]
    assert _read_state_providers(state_db) == ["codex-lb", "codex-lb", "codex-lb"]
    assert result.methods_used == ("jsonl", "sqlite")
    assert result.jsonl_files_updated == 1
    assert result.sqlite_rows_updated == 2
    assert result.backup_path is not None
    assert (result.backup_path / "state_5.sqlite").is_file()
    assert (result.backup_path / "sessions" / "2026" / "session.jsonl").is_file()
    assert ProviderCount("openai", 3) in result.provider_counts_before
    assert ProviderCount("codex-lb", 5) in result.provider_counts_after


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
    monkeypatch.setattr(codex_sessions_retag, "_sqlite_count_provider_rows", lambda _path, _provider: 1)

    result = retag_codex_sessions(
        codex_home=codex_home,
        source_provider="openai",
        target_provider="codex-lb",
    )

    assert result.sqlite_rows_updated == 1
    assert _read_state_providers(state_db) == ["codex-lb"]


def test_read_only_sqlite_connection_uses_immutable_uri(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
    assert "immutable=1" in calls[0][0]


def test_sqlite_backup_consolidates_wal_rows(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    state_db = codex_home / "state_5.sqlite"
    codex_home.mkdir()
    with sqlite3.connect(state_db) as conn:
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


def test_sqlite_copy_fallback_uses_consolidated_backup_and_removes_sidecars(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    state_db = codex_home / "state_5.sqlite"
    codex_home.mkdir()
    with sqlite3.connect(state_db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT)")
        conn.execute("INSERT INTO threads (id, model_provider) VALUES ('thread-1', 'openai')")
        conn.commit()
        assert Path(f"{state_db}-wal").stat().st_size > 0

        temp_path = codex_sessions_retag._copy_sqlite_to_temp(state_db)

    try:
        assert _read_state_providers(temp_path) == ["openai"]
        codex_sessions_retag._update_sqlite_provider_in_place(temp_path, "openai", "codex-lb")
        Path(f"{state_db}-wal").write_bytes(b"stale wal")
        Path(f"{state_db}-shm").write_bytes(b"stale shm")
        codex_sessions_retag._replace_sqlite_db(temp_path, state_db)

        assert _read_state_providers(state_db) == ["codex-lb"]
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
