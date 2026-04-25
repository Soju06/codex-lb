from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote

JsonObject = dict[str, object]
ProgressLogger = Callable[[str], None]

PROVIDER_RETAG_BACKUP_DIR = "provider-retag"
_SUPPORTED_PROVIDERS = {"openai", "codex-lb"}
_STATE_DB_PATTERN = "state_*.sqlite"


@dataclass(frozen=True)
class ProviderCount:
    provider: str
    count: int


@dataclass(frozen=True)
class RetagResult:
    codex_home: Path
    source_provider: str
    target_provider: str
    dry_run: bool
    methods_used: tuple[str, ...]
    backup_path: Path | None
    jsonl_files_scanned: int
    jsonl_files_matched: int
    jsonl_files_updated: int
    sqlite_dbs_scanned: int
    sqlite_dbs_matched: int
    sqlite_rows_matched: int
    sqlite_rows_updated: int
    provider_counts_before: tuple[ProviderCount, ...]
    provider_counts_after: tuple[ProviderCount, ...]
    logs: tuple[str, ...]


def default_codex_home() -> Path:
    """Pick the Codex data path for this command without changing app-wide settings."""
    env_path = os.getenv("CODEX_HOME")
    if env_path:
        return Path(env_path).expanduser()
    if _running_in_container():
        return Path("/codex-home")
    if _running_in_wsl():
        windows_home = _discover_wsl_windows_codex_home()
        if windows_home is not None:
            return windows_home
    return Path.home() / ".codex"


def retag_codex_sessions(
    *,
    codex_home: Path,
    source_provider: str,
    target_provider: str,
    dry_run: bool = False,
    progress_logger: ProgressLogger | None = None,
) -> RetagResult:
    logs: list[str] = []

    def log(message: str) -> None:
        logs.append(message)
        if progress_logger is not None:
            progress_logger(message)

    source_provider = _normalize_provider(source_provider)
    target_provider = _normalize_provider(target_provider)
    _validate_providers(source_provider, target_provider)

    codex_home = codex_home.expanduser().resolve()
    sessions_dir = codex_home / "sessions"
    log(f"Using Codex home {codex_home}")
    log(f"Retagging Codex sessions from {source_provider} to {target_provider}")

    # Build the full write set before taking a backup so dry-runs and real
    # retags report the same targets.
    jsonl_files = tuple(_find_jsonl_session_files(sessions_dir))
    state_dbs = tuple(_find_state_dbs(codex_home))
    provider_counts_before = _provider_counts(codex_home)
    jsonl_files_to_update = tuple(path for path in jsonl_files if _jsonl_contains_provider(path, source_provider))
    sqlite_dbs_to_update = tuple(db for db in state_dbs if _sqlite_count_provider_rows(db, source_provider) > 0)
    sqlite_rows_matched = sum(_sqlite_count_provider_rows(db, source_provider) for db in sqlite_dbs_to_update)

    methods_used = _methods_used(jsonl_files_to_update, sqlite_dbs_to_update)
    log(f"JSONL sessions method scanned {len(jsonl_files)} files under {sessions_dir}")
    log(f"SQLite state DB method scanned {len(state_dbs)} state database file(s)")

    backup_path: Path | None = None
    jsonl_files_updated = 0
    sqlite_rows_updated = 0

    if dry_run:
        log("Dry run enabled; no files will be changed")
    elif jsonl_files_to_update or sqlite_dbs_to_update:
        backup_path = _create_backup(codex_home, jsonl_files_to_update, sqlite_dbs_to_update)
        log(f"Created backup at {backup_path}")

        for path in jsonl_files_to_update:
            if _retag_jsonl_file(path, source_provider, target_provider):
                jsonl_files_updated += 1
        if jsonl_files_to_update:
            log(f"Updated {jsonl_files_updated} JSONL session file(s)")

        for db_path in sqlite_dbs_to_update:
            sqlite_rows_updated += _update_sqlite_provider(db_path, source_provider, target_provider)
        if sqlite_dbs_to_update:
            log(f"Updated {sqlite_rows_updated} SQLite thread row(s)")
    else:
        log(f"No {source_provider} Codex session tags were found")

    provider_counts_after = provider_counts_before if dry_run else _provider_counts(codex_home)

    return RetagResult(
        codex_home=codex_home,
        source_provider=source_provider,
        target_provider=target_provider,
        dry_run=dry_run,
        methods_used=methods_used,
        backup_path=backup_path,
        jsonl_files_scanned=len(jsonl_files),
        jsonl_files_matched=len(jsonl_files_to_update),
        jsonl_files_updated=jsonl_files_updated,
        sqlite_dbs_scanned=len(state_dbs),
        sqlite_dbs_matched=len(sqlite_dbs_to_update),
        sqlite_rows_matched=sqlite_rows_matched,
        sqlite_rows_updated=sqlite_rows_updated,
        provider_counts_before=provider_counts_before,
        provider_counts_after=provider_counts_after,
        logs=tuple(logs),
    )


def _normalize_provider(provider: str) -> str:
    return provider.strip()


def _validate_providers(source_provider: str, target_provider: str) -> None:
    if source_provider == target_provider:
        raise ValueError("--from and --to must be different providers")
    unknown = {source_provider, target_provider} - _SUPPORTED_PROVIDERS
    if unknown:
        supported = ", ".join(sorted(_SUPPORTED_PROVIDERS))
        raise ValueError(f"unsupported provider {', '.join(sorted(unknown))}; expected one of: {supported}")


def _running_in_container() -> bool:
    # Keep cgroup marker detection scoped to this CLI command; app settings use
    # their existing runtime checks.
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    return _cgroup_mentions_container(Path("/proc/1/cgroup"))


def _cgroup_mentions_container(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    markers = ("docker", "kubepods", "containerd", "libpod")
    return any(marker in text for marker in markers)


def _running_in_wsl() -> bool:
    release = ""
    try:
        release = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        pass
    return "microsoft" in release.casefold() or "WSL_DISTRO_NAME" in os.environ


def _discover_wsl_windows_codex_home() -> Path | None:
    userprofile = os.getenv("USERPROFILE")
    if not userprofile:
        return None
    codex_home = _wsl_path_from_windows_userprofile(userprofile) / ".codex"
    if codex_home.is_dir():
        return codex_home
    return None


def _wsl_path_from_windows_userprofile(userprofile: str) -> Path:
    normalized = userprofile.replace("\\", "/")
    match = re.fullmatch(r"([A-Za-z]):/(.*)", normalized)
    if match is None:
        return Path(normalized).expanduser()
    drive, tail = match.groups()
    parts = [part for part in tail.split("/") if part]
    return Path("/mnt") / drive.lower() / Path(*parts)


def _find_jsonl_session_files(sessions_dir: Path) -> tuple[Path, ...]:
    if not sessions_dir.is_dir():
        return ()
    return tuple(sorted(path for path in sessions_dir.rglob("*.jsonl") if path.is_file()))


def _find_state_dbs(codex_home: Path) -> tuple[Path, ...]:
    state_dbs = (path for path in codex_home.glob(_STATE_DB_PATTERN) if path.is_file())
    return tuple(sorted(state_dbs, key=_state_db_sort_key))


def _state_db_sort_key(path: Path) -> tuple[int, str]:
    match = re.fullmatch(r"state_(\d+)\.sqlite", path.name)
    version = int(match.group(1)) if match else -1
    return version, path.name


def _jsonl_contains_provider(path: Path, provider: str) -> bool:
    return any(record.get("model_provider") == provider for record in _read_jsonl_records(path))


def _retag_jsonl_file(path: Path, source_provider: str, target_provider: str) -> bool:
    changed = False
    output_lines: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if not line:
                output_lines.append(raw_line)
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                output_lines.append(raw_line)
                continue
            if isinstance(record, dict) and record.get("model_provider") == source_provider:
                # Preserve invalid or unrelated JSONL lines verbatim; only
                # matched session records are normalized through json.dumps.
                record["model_provider"] = target_provider
                changed = True
                output_lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            else:
                output_lines.append(raw_line)
    if not changed:
        return False
    _write_text_atomically(path, "".join(output_lines))
    return True


def _read_jsonl_records(path: Path) -> Iterable[JsonObject]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def _sqlite_count_provider_rows(db_path: Path, provider: str) -> int:
    try:
        with _connect_sqlite(db_path, read_only=True, immutable=True) as conn:
            if not _sqlite_has_threads_table(conn):
                return 0
            row = conn.execute("SELECT COUNT(*) FROM threads WHERE model_provider = ?", (provider,)).fetchone()
            return int(row[0]) if row is not None else 0
    except sqlite3.OperationalError as exc:
        if "unable to open database file" not in str(exc).casefold():
            raise
        return _sqlite_count_provider_rows_via_copy(db_path, provider)


def _update_sqlite_provider(db_path: Path, source_provider: str, target_provider: str) -> int:
    try:
        return _update_sqlite_provider_in_place(db_path, source_provider, target_provider)
    except sqlite3.OperationalError as exc:
        if "unable to open database file" not in str(exc).casefold():
            raise
        # Some bind mounts reject direct SQLite writes from inside a container.
        # Updating a sibling copy and moving it back keeps the operation scoped
        # to the mounted Codex home.
        return _update_sqlite_provider_via_copy(db_path, source_provider, target_provider)


def _update_sqlite_provider_in_place(db_path: Path, source_provider: str, target_provider: str) -> int:
    with _connect_sqlite(db_path) as conn:
        if not _sqlite_has_threads_table(conn):
            return 0
        cursor = conn.execute(
            "UPDATE threads SET model_provider = ? WHERE model_provider = ?",
            (target_provider, source_provider),
        )
        conn.commit()
        return int(cursor.rowcount if cursor.rowcount != -1 else 0)


def _update_sqlite_provider_via_copy(db_path: Path, source_provider: str, target_provider: str) -> int:
    temp_path = _copy_sqlite_to_temp(db_path)
    try:
        updated = _update_sqlite_provider_in_place(temp_path, source_provider, target_provider)
        if updated:
            _replace_sqlite_db(temp_path, db_path)
        return updated
    finally:
        temp_path.unlink(missing_ok=True)


def _sqlite_count_provider_rows_via_copy(db_path: Path, provider: str) -> int:
    temp_path = _copy_sqlite_to_temp(db_path)
    try:
        return _sqlite_count_provider_rows(temp_path, provider)
    finally:
        temp_path.unlink(missing_ok=True)


def _copy_sqlite_to_temp(db_path: Path) -> Path:
    temp_dir = db_path.parent / ".tmp" / PROVIDER_RETAG_BACKUP_DIR
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{db_path.stem}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.sqlite"
    _backup_sqlite_db(db_path, temp_path)
    return temp_path


def _replace_sqlite_db(source: Path, destination: Path) -> None:
    _consolidate_sqlite_db(source)
    shutil.copy2(source, destination)
    for sidecar in _sqlite_sidecar_paths(destination):
        sidecar.unlink(missing_ok=True)


def _connect_sqlite(db_path: Path, *, read_only: bool = False, immutable: bool = False) -> sqlite3.Connection:
    target = str(db_path)
    if read_only:
        quoted_path = quote(str(db_path.resolve()), safe="/:")
        immutable_flag = "&immutable=1" if immutable else ""
        target = f"file:{quoted_path}?mode=ro{immutable_flag}"
    conn = sqlite3.connect(target, timeout=5, uri=read_only)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _sqlite_has_threads_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'threads'",
    ).fetchone()
    return row is not None


def _provider_counts(codex_home: Path) -> tuple[ProviderCount, ...]:
    counts: dict[str, int] = {}
    for path in _find_jsonl_session_files(codex_home / "sessions"):
        for record in _read_jsonl_records(path):
            provider = record.get("model_provider")
            if isinstance(provider, str):
                counts[provider] = counts.get(provider, 0) + 1
    for db_path in _find_state_dbs(codex_home):
        try:
            with _connect_sqlite(db_path, read_only=True, immutable=True) as conn:
                if not _sqlite_has_threads_table(conn):
                    continue
                rows = conn.execute(
                    "SELECT model_provider, COUNT(*) FROM threads GROUP BY model_provider",
                ).fetchall()
                for provider, count in rows:
                    if isinstance(provider, str):
                        counts[provider] = counts.get(provider, 0) + int(count)
        except sqlite3.OperationalError as exc:
            if "unable to open database file" not in str(exc).casefold():
                raise
    return tuple(ProviderCount(provider, count) for provider, count in sorted(counts.items()))


def _methods_used(jsonl_files: Sequence[Path], state_dbs: Sequence[Path]) -> tuple[str, ...]:
    methods: list[str] = []
    if jsonl_files:
        methods.append("jsonl")
    if state_dbs:
        methods.append("sqlite")
    return tuple(methods)


def _create_backup(codex_home: Path, jsonl_files: Sequence[Path], state_dbs: Sequence[Path]) -> Path:
    backup_dir = _next_backup_dir(codex_home / "backups" / PROVIDER_RETAG_BACKUP_DIR)
    backup_dir.mkdir(parents=True)

    for db_path in state_dbs:
        _backup_sqlite_db(db_path, backup_dir / db_path.name)

    session_index = codex_home / "session_index.jsonl"
    if session_index.is_file():
        shutil.copy2(session_index, backup_dir / session_index.name)

    for path in jsonl_files:
        destination = backup_dir / path.relative_to(codex_home)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)

    return backup_dir


def _backup_sqlite_db(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _connect_sqlite(source, read_only=True) as source_conn, sqlite3.connect(str(destination)) as backup_conn:
        source_conn.backup(backup_conn)
        backup_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        backup_conn.execute("PRAGMA journal_mode=DELETE")
    for sidecar in _sqlite_sidecar_paths(destination):
        sidecar.unlink(missing_ok=True)


def _consolidate_sqlite_db(db_path: Path) -> None:
    with _connect_sqlite(db_path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")


def _sqlite_sidecar_paths(db_path: Path) -> tuple[Path, Path]:
    return Path(f"{db_path}-wal"), Path(f"{db_path}-shm")


def _next_backup_dir(base_dir: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    candidate = base_dir / stamp
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = base_dir / f"{stamp}-{suffix}"
    return candidate


def _write_text_atomically(path: Path, text: str) -> None:
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    temp_path.replace(path)
