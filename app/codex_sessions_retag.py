from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast
from urllib.parse import quote

JsonObject = dict[str, object]
ProgressLogger = Callable[[str], None]
ByteProgress = Callable[[int, int, str], None]

PROVIDER_RETAG_BACKUP_DIR = "provider-retag"
PROGRESS_PREFIX = "CODEX_LB_RETAG_PROGRESS "
_SUPPORTED_PROVIDERS = {"openai", "codex-lb"}
_STATE_DB_PATTERN = "state_*.sqlite"
_STATE_DB_NAME_PATTERN = re.compile(r"state_(\d+)\.sqlite")
_COPY_CHUNK_SIZE = 4 * 1024 * 1024
_SQLITE_UPDATE_BATCH_SIZE = 1_000
_MAX_SESSION_METADATA_BYTES = 1024 * 1024
_UTF8_BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class ProviderCount:
    provider: str
    count: int


@dataclass(frozen=True)
class _JsonlSessionPlan:
    path: Path
    session_id: str | None
    provider: str | None
    metadata_line_index: int | None
    size_bytes: int


@dataclass(frozen=True)
class _SqliteDbPlan:
    path: Path
    provider_counts: tuple[ProviderCount, ...]


@dataclass(frozen=True)
class _SqliteThreadPlan:
    db_path: Path
    session_id: str
    provider: str


@dataclass(frozen=True)
class _RetagPlan:
    jsonl_sessions: tuple[_JsonlSessionPlan, ...]
    jsonl_targets: tuple[_JsonlSessionPlan, ...]
    sqlite_dbs: tuple[_SqliteDbPlan, ...]
    sqlite_targets: tuple[_SqliteDbPlan, ...]
    provider_counts_before: tuple[ProviderCount, ...]
    sqlite_rows_matched: int


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


@dataclass(frozen=True)
class SessionMetadataMismatch:
    """A partial provider-tag disagreement for one logical Codex session."""

    session_id: str
    active_provider: str
    opposite_provider: str
    jsonl_paths: tuple[Path, ...]
    sqlite_db_paths: tuple[Path, ...]


@dataclass(frozen=True)
class SessionMetadataRepairPreview:
    codex_home: Path
    active_provider: str
    mismatches: tuple[SessionMetadataMismatch, ...]
    jsonl_files_scanned: int
    sqlite_dbs_scanned: int


@dataclass(frozen=True)
class SessionMetadataRepairResult:
    codex_home: Path
    active_provider: str
    session_ids: tuple[str, ...]
    dry_run: bool
    backup_path: Path | None
    jsonl_files_updated: int
    sqlite_rows_updated: int


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

    def emit_progress(phase: str, completed: int, total: int, unit: str, message: str) -> None:
        _emit_structured_progress(progress_logger, phase, completed, total, unit, message)

    source_provider = _normalize_provider(source_provider)
    target_provider = _normalize_provider(target_provider)
    _validate_providers(source_provider, target_provider)

    codex_home = codex_home.expanduser().resolve()
    sessions_dir = codex_home / "sessions"
    log(f"Using Codex home {codex_home}")
    log(f"Retagging Codex sessions from {source_provider} to {target_provider}")

    plan = _build_retag_plan(
        codex_home,
        source_provider,
        lambda completed, total, message: emit_progress("discovery", completed, total, "items", message),
    )

    methods_used = _methods_used(
        tuple(target.path for target in plan.jsonl_targets),
        tuple(target.path for target in plan.sqlite_targets),
    )
    log(f"JSONL sessions method scanned {len(plan.jsonl_sessions)} files under {sessions_dir}")
    unknown_jsonl_sessions = sum(session.provider is None for session in plan.jsonl_sessions)
    if unknown_jsonl_sessions:
        log(
            f"JSONL session metadata was unavailable in {unknown_jsonl_sessions} file(s); "
            "transcript content was not scanned"
        )
    log(f"SQLite state DB method scanned {len(plan.sqlite_dbs)} state database file(s)")

    backup_path: Path | None = None
    jsonl_files_updated = 0
    sqlite_rows_updated = 0

    if dry_run:
        log("Dry run enabled; no files will be changed")
    elif plan.jsonl_targets or plan.sqlite_targets:
        backup_path = _create_backup(
            codex_home,
            tuple(target.path for target in plan.jsonl_targets),
            tuple(target.path for target in plan.sqlite_targets),
            progress_callback=lambda completed, total, message: emit_progress(
                "backup",
                completed,
                total,
                "bytes",
                message,
            ),
        )
        log(f"Created backup at {backup_path}")

        jsonl_total_bytes = sum(target.size_bytes for target in plan.jsonl_targets)
        jsonl_completed_bytes = 0
        if plan.jsonl_targets:
            emit_progress("jsonl_rewrite", 0, jsonl_total_bytes, "bytes", "Starting JSONL rewrite")
        for target in plan.jsonl_targets:
            assert target.metadata_line_index is not None
            base_completed = jsonl_completed_bytes
            if _retag_jsonl_file(
                target.path,
                source_provider,
                target_provider,
                target.metadata_line_index,
                progress_callback=lambda completed, _total, message, base=base_completed: emit_progress(
                    "jsonl_rewrite",
                    min(base + completed, jsonl_total_bytes),
                    jsonl_total_bytes,
                    "bytes",
                    message,
                ),
            ):
                jsonl_files_updated += 1
            jsonl_completed_bytes += target.size_bytes
            emit_progress(
                "jsonl_rewrite",
                jsonl_completed_bytes,
                jsonl_total_bytes,
                "bytes",
                f"Rewrote {target.path.name}",
            )
        if plan.jsonl_targets:
            log(f"Updated {jsonl_files_updated} JSONL session file(s)")

        if plan.sqlite_targets:
            emit_progress("sqlite_update", 0, plan.sqlite_rows_matched, "rows", "Starting SQLite update")
        for target in plan.sqlite_targets:
            expected_rows = _provider_count(target.provider_counts, source_provider)
            base_updated = sqlite_rows_updated
            sqlite_rows_updated += _update_sqlite_provider(
                target.path,
                source_provider,
                target_provider,
                expected_rows=expected_rows,
                progress_callback=lambda completed, _total, message, base=base_updated: emit_progress(
                    "sqlite_update",
                    min(base + completed, plan.sqlite_rows_matched),
                    plan.sqlite_rows_matched,
                    "rows",
                    message,
                ),
            )
            emit_progress(
                "sqlite_update",
                sqlite_rows_updated,
                plan.sqlite_rows_matched,
                "rows",
                f"Updated {target.path.name}",
            )
        if plan.sqlite_targets:
            log(f"Updated {sqlite_rows_updated} SQLite thread row(s)")

        _verify_retag_plan(
            plan,
            source_provider,
            target_provider,
            lambda completed, total, message: emit_progress(
                "verification",
                completed,
                total,
                "items",
                message,
            ),
        )
    else:
        log(f"No {source_provider} Codex session tags were found")

    provider_counts_after = (
        plan.provider_counts_before
        if dry_run
        else _derive_provider_counts_after(
            plan.provider_counts_before,
            source_provider,
            target_provider,
            len(plan.jsonl_targets) + plan.sqlite_rows_matched,
        )
    )

    return RetagResult(
        codex_home=codex_home,
        source_provider=source_provider,
        target_provider=target_provider,
        dry_run=dry_run,
        methods_used=methods_used,
        backup_path=backup_path,
        jsonl_files_scanned=len(plan.jsonl_sessions),
        jsonl_files_matched=len(plan.jsonl_targets),
        jsonl_files_updated=jsonl_files_updated,
        sqlite_dbs_scanned=len(plan.sqlite_dbs),
        sqlite_dbs_matched=len(plan.sqlite_targets),
        sqlite_rows_matched=plan.sqlite_rows_matched,
        sqlite_rows_updated=sqlite_rows_updated,
        provider_counts_before=plan.provider_counts_before,
        provider_counts_after=provider_counts_after,
        logs=tuple(logs),
    )


def preview_session_metadata_mismatches(
    *,
    codex_home: Path,
    active_provider: str,
) -> SessionMetadataRepairPreview:
    """Return only sessions with conflicting supported provider tags.

    This deliberately reads bounded JSONL metadata and SQLite thread rows only;
    it does not inspect transcript records or make any filesystem changes.
    """
    active_provider = _normalize_provider(active_provider)
    codex_home = codex_home.expanduser().resolve()
    jsonl_sessions = tuple(_discover_jsonl_session(path) for path in _find_jsonl_session_files(codex_home / "sessions"))
    sqlite_dbs = _find_state_dbs(codex_home)
    sqlite_threads = tuple(thread for db_path in sqlite_dbs for thread in _sqlite_thread_plans(db_path))
    opposite_provider = _opposite_provider(active_provider)

    jsonl_by_session: dict[str, list[_JsonlSessionPlan]] = {}
    for session in jsonl_sessions:
        if session.session_id is not None and session.provider is not None:
            jsonl_by_session.setdefault(session.session_id, []).append(session)
    sqlite_by_session: dict[str, list[_SqliteThreadPlan]] = {}
    for thread in sqlite_threads:
        sqlite_by_session.setdefault(thread.session_id, []).append(thread)

    mismatches: list[SessionMetadataMismatch] = []
    for session_id in sorted(set(jsonl_by_session) | set(sqlite_by_session)):
        jsonl_entries = tuple(jsonl_by_session.get(session_id, ()))
        sqlite_entries = tuple(sqlite_by_session.get(session_id, ()))
        providers = {entry.provider for entry in jsonl_entries} | {entry.provider for entry in sqlite_entries}
        if providers != {active_provider, opposite_provider}:
            continue
        mismatches.append(
            SessionMetadataMismatch(
                session_id=session_id,
                active_provider=active_provider,
                opposite_provider=opposite_provider,
                jsonl_paths=tuple(entry.path for entry in jsonl_entries if entry.provider == opposite_provider),
                sqlite_db_paths=tuple(entry.db_path for entry in sqlite_entries if entry.provider == opposite_provider),
            )
        )

    return SessionMetadataRepairPreview(
        codex_home=codex_home,
        active_provider=active_provider,
        mismatches=tuple(mismatches),
        jsonl_files_scanned=len(jsonl_sessions),
        sqlite_dbs_scanned=len(sqlite_dbs),
    )


def repair_session_metadata_mismatches(
    *,
    codex_home: Path,
    active_provider: str,
    session_ids: Sequence[str],
    dry_run: bool = False,
    progress_logger: ProgressLogger | None = None,
) -> SessionMetadataRepairResult:
    """Repair only explicitly supplied, currently eligible mismatch session IDs."""

    def emit_progress(phase: str, completed: int, total: int, unit: str, message: str) -> None:
        _emit_structured_progress(progress_logger, phase, completed, total, unit, message)

    normalized_ids = tuple(dict.fromkeys(session_id.strip() for session_id in session_ids if session_id.strip()))
    if not normalized_ids:
        raise ValueError("at least one session ID is required for targeted metadata repair")
    preview = preview_session_metadata_mismatches(codex_home=codex_home, active_provider=active_provider)
    candidates = {mismatch.session_id: mismatch for mismatch in preview.mismatches}
    unknown_ids = tuple(session_id for session_id in normalized_ids if session_id not in candidates)
    if unknown_ids:
        raise ValueError("session metadata mismatch preview changed or is ineligible: " + ", ".join(unknown_ids))

    opposite_provider = _opposite_provider(preview.active_provider)
    jsonl_sessions = tuple(
        _discover_jsonl_session(path) for path in _find_jsonl_session_files(preview.codex_home / "sessions")
    )
    sqlite_threads = tuple(
        thread for db_path in _find_state_dbs(preview.codex_home) for thread in _sqlite_thread_plans(db_path)
    )
    jsonl_targets = tuple(
        session
        for session in jsonl_sessions
        if session.session_id in normalized_ids
        and session.provider == opposite_provider
        and session.metadata_line_index is not None
    )
    sqlite_targets = tuple(
        thread
        for thread in sqlite_threads
        if thread.session_id in normalized_ids and thread.provider == opposite_provider
    )
    expected_components = sum(
        len(candidates[session_id].jsonl_paths) + len(candidates[session_id].sqlite_db_paths)
        for session_id in normalized_ids
    )
    if len(jsonl_targets) + len(sqlite_targets) != expected_components:
        raise RuntimeError("session metadata changed after preview; no files were modified")

    if dry_run:
        return SessionMetadataRepairResult(
            codex_home=preview.codex_home,
            active_provider=preview.active_provider,
            session_ids=normalized_ids,
            dry_run=True,
            backup_path=None,
            jsonl_files_updated=0,
            sqlite_rows_updated=0,
        )

    backup_path = _create_backup(
        preview.codex_home,
        tuple(target.path for target in jsonl_targets),
        tuple(dict.fromkeys(target.db_path for target in sqlite_targets)),
        progress_callback=lambda completed, total, message: emit_progress(
            "backup",
            completed,
            total,
            "bytes",
            message,
        ),
    )
    try:
        jsonl_total_bytes = sum(target.size_bytes for target in jsonl_targets)
        jsonl_completed_bytes = 0
        if jsonl_targets:
            emit_progress("jsonl_rewrite", 0, jsonl_total_bytes, "bytes", "Starting targeted JSONL rewrite")
        for target in jsonl_targets:
            assert target.metadata_line_index is not None
            base_completed = jsonl_completed_bytes
            _retag_jsonl_file(
                target.path,
                opposite_provider,
                preview.active_provider,
                target.metadata_line_index,
                progress_callback=lambda completed, _total, message, base=base_completed: emit_progress(
                    "jsonl_rewrite",
                    min(base + completed, jsonl_total_bytes),
                    jsonl_total_bytes,
                    "bytes",
                    message,
                ),
            )
            jsonl_completed_bytes += target.size_bytes
            emit_progress(
                "jsonl_rewrite",
                jsonl_completed_bytes,
                jsonl_total_bytes,
                "bytes",
                f"Rewrote {target.path.name}",
            )
        sqlite_rows_updated = 0
        if sqlite_targets:
            emit_progress("sqlite_update", 0, len(sqlite_targets), "rows", "Starting targeted SQLite update")
        for target in sqlite_targets:
            sqlite_rows_updated += _update_sqlite_thread_provider(
                target.db_path,
                target.session_id,
                opposite_provider,
                preview.active_provider,
            )
            emit_progress(
                "sqlite_update",
                sqlite_rows_updated,
                len(sqlite_targets),
                "rows",
                f"Updated {target.db_path.name}",
            )
        verification_items = len(jsonl_targets) + len(sqlite_targets)
        emit_progress("verification", 0, verification_items, "items", "Verifying targeted metadata repair")
        _verify_targeted_metadata_repair(
            preview.codex_home,
            preview.active_provider,
            normalized_ids,
            jsonl_targets,
            sqlite_targets,
        )
        emit_progress(
            "verification",
            verification_items,
            verification_items,
            "items",
            "Verified targeted metadata repair",
        )
    except Exception:
        _restore_backup(preview.codex_home, backup_path)
        raise

    return SessionMetadataRepairResult(
        codex_home=preview.codex_home,
        active_provider=preview.active_provider,
        session_ids=normalized_ids,
        dry_run=False,
        backup_path=backup_path,
        jsonl_files_updated=len(jsonl_targets),
        sqlite_rows_updated=sqlite_rows_updated,
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
    state_dbs = (
        path
        for path in codex_home.glob(_STATE_DB_PATTERN)
        if path.is_file() and _STATE_DB_NAME_PATTERN.fullmatch(path.name)
    )
    return tuple(sorted(state_dbs, key=_state_db_sort_key))


def _state_db_sort_key(path: Path) -> tuple[int, str]:
    match = _STATE_DB_NAME_PATTERN.fullmatch(path.name)
    version = int(match.group(1)) if match else -1
    return version, path.name


def _build_retag_plan(
    codex_home: Path,
    source_provider: str,
    progress_callback: ByteProgress | None = None,
) -> _RetagPlan:
    jsonl_files = _find_jsonl_session_files(codex_home / "sessions")
    state_dbs = _find_state_dbs(codex_home)
    total_items = len(jsonl_files) + len(state_dbs)
    completed_items = 0
    _report_progress(progress_callback, completed_items, total_items, "Discovering session metadata")

    aggregate_counts: dict[str, int] = {}
    jsonl_sessions: list[_JsonlSessionPlan] = []
    for path in jsonl_files:
        session = _discover_jsonl_session(path)
        jsonl_sessions.append(session)
        if session.provider is not None:
            aggregate_counts[session.provider] = aggregate_counts.get(session.provider, 0) + 1
        completed_items += 1
        _report_progress(progress_callback, completed_items, total_items, f"Discovered {path.name}")

    sqlite_dbs: list[_SqliteDbPlan] = []
    for path in state_dbs:
        provider_counts = _sqlite_provider_counts(path)
        sqlite_dbs.append(_SqliteDbPlan(path=path, provider_counts=provider_counts))
        for provider_count in provider_counts:
            aggregate_counts[provider_count.provider] = (
                aggregate_counts.get(provider_count.provider, 0) + provider_count.count
            )
        completed_items += 1
        _report_progress(progress_callback, completed_items, total_items, f"Inspected {path.name}")

    jsonl_sessions_tuple = tuple(jsonl_sessions)
    sqlite_dbs_tuple = tuple(sqlite_dbs)
    jsonl_targets = tuple(session for session in jsonl_sessions_tuple if session.provider == source_provider)
    sqlite_targets = tuple(db for db in sqlite_dbs_tuple if _provider_count(db.provider_counts, source_provider) > 0)
    sqlite_rows_matched = sum(_provider_count(db.provider_counts, source_provider) for db in sqlite_targets)
    return _RetagPlan(
        jsonl_sessions=jsonl_sessions_tuple,
        jsonl_targets=jsonl_targets,
        sqlite_dbs=sqlite_dbs_tuple,
        sqlite_targets=sqlite_targets,
        provider_counts_before=_provider_counts_tuple(aggregate_counts),
        sqlite_rows_matched=sqlite_rows_matched,
    )


def _discover_jsonl_session(path: Path) -> _JsonlSessionPlan:
    size_bytes = path.stat().st_size
    with path.open("rb") as handle:
        raw_line = handle.readline(_MAX_SESSION_METADATA_BYTES + 1)
    if not raw_line or len(raw_line) > _MAX_SESSION_METADATA_BYTES:
        return _JsonlSessionPlan(
            path=path,
            session_id=None,
            provider=None,
            metadata_line_index=None,
            size_bytes=size_bytes,
        )
    try:
        record = json.loads(raw_line.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _JsonlSessionPlan(
            path=path,
            session_id=None,
            provider=None,
            metadata_line_index=None,
            size_bytes=size_bytes,
        )
    if isinstance(record, dict):
        provider = _jsonl_record_provider(record)
        if provider is not None:
            return _JsonlSessionPlan(
                path=path,
                session_id=_jsonl_record_session_id(record),
                provider=provider,
                metadata_line_index=0,
                size_bytes=size_bytes,
            )
    return _JsonlSessionPlan(path=path, session_id=None, provider=None, metadata_line_index=None, size_bytes=size_bytes)


def _retag_jsonl_file(
    path: Path,
    source_provider: str,
    target_provider: str,
    metadata_line_index: int,
    *,
    progress_callback: ByteProgress | None = None,
) -> bool:
    total_bytes = path.stat().st_size
    completed_bytes = 0
    temp_path: Path | None = None
    try:
        with (
            path.open("rb") as input_handle,
            NamedTemporaryFile("wb", dir=path.parent, delete=False) as output_handle,
        ):
            temp_path = Path(output_handle.name)
            for line_index in range(metadata_line_index + 1):
                raw_line = input_handle.readline()
                if not raw_line:
                    raise RuntimeError(f"session metadata disappeared from {path}")
                completed_bytes += len(raw_line)
                if line_index == metadata_line_index:
                    output_handle.write(_retag_jsonl_metadata_line(raw_line, source_provider, target_provider, path))
                else:
                    output_handle.write(raw_line)
                _report_progress(progress_callback, completed_bytes, total_bytes, f"Rewriting {path.name}")

            while chunk := input_handle.read(_COPY_CHUNK_SIZE):
                output_handle.write(chunk)
                completed_bytes += len(chunk)
                _report_progress(progress_callback, completed_bytes, total_bytes, f"Rewriting {path.name}")
            output_handle.flush()
            os.fsync(output_handle.fileno())
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise
    assert temp_path is not None
    try:
        shutil.copystat(path, temp_path)
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    _report_progress(progress_callback, total_bytes, total_bytes, f"Rewrote {path.name}")
    return True


def _retag_jsonl_metadata_line(
    raw_line: bytes,
    source_provider: str,
    target_provider: str,
    path: Path,
) -> bytes:
    if raw_line.endswith(b"\r\n"):
        body, newline = raw_line[:-2], b"\r\n"
    elif raw_line.endswith(b"\n"):
        body, newline = raw_line[:-1], b"\n"
    else:
        body, newline = raw_line, b""
    has_bom = body.startswith(_UTF8_BOM)
    try:
        record = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"planned session metadata is no longer readable in {path}") from exc
    if not isinstance(record, dict) or not _retag_jsonl_record_provider(
        record,
        source_provider,
        target_provider,
    ):
        raise RuntimeError(f"planned {source_provider} session metadata changed in {path}")
    encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if has_bom:
        encoded = _UTF8_BOM + encoded
    return encoded + newline


def _jsonl_record_provider(record: JsonObject) -> str | None:
    provider = record.get("model_provider")
    if isinstance(provider, str):
        return provider
    if record.get("type") != "session_meta":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    payload = cast(JsonObject, payload)
    payload_provider = payload.get("model_provider")
    return payload_provider if isinstance(payload_provider, str) else None


def _jsonl_record_session_id(record: JsonObject) -> str | None:
    record_id = record.get("id")
    if isinstance(record_id, str) and record_id:
        return record_id
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    payload_id = payload.get("id")
    return payload_id if isinstance(payload_id, str) and payload_id else None


def _retag_jsonl_record_provider(record: JsonObject, source_provider: str, target_provider: str) -> bool:
    if record.get("model_provider") == source_provider:
        record["model_provider"] = target_provider
        return True
    if record.get("type") != "session_meta":
        return False
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return False
    payload = cast(JsonObject, payload)
    if payload.get("model_provider") != source_provider:
        return False
    payload["model_provider"] = target_provider
    return True


def _sqlite_provider_counts(db_path: Path) -> tuple[ProviderCount, ...]:
    try:
        with closing(_connect_sqlite(db_path, read_only=True)) as conn:
            if not _sqlite_has_threads_table(conn):
                return ()
            if not _sqlite_has_model_provider_column(conn):
                return ()
            rows = conn.execute(
                "SELECT model_provider, COUNT(*) FROM threads GROUP BY model_provider",
            ).fetchall()
            return tuple(ProviderCount(provider, int(count)) for provider, count in rows if isinstance(provider, str))
    except sqlite3.OperationalError as exc:
        if "unable to open database file" not in str(exc).casefold():
            raise
        return _sqlite_provider_counts_via_copy(db_path)


def _sqlite_count_provider_rows(db_path: Path, provider: str) -> int:
    return _provider_count(_sqlite_provider_counts(db_path), provider)


def _sqlite_thread_plans(db_path: Path) -> tuple[_SqliteThreadPlan, ...]:
    try:
        with closing(_connect_sqlite(db_path, read_only=True)) as conn:
            if not _sqlite_has_threads_table(conn) or not _sqlite_has_model_provider_column(conn):
                return ()
            rows = conn.execute("SELECT id, model_provider FROM threads").fetchall()
            return tuple(
                _SqliteThreadPlan(db_path=db_path, session_id=session_id, provider=provider)
                for session_id, provider in rows
                if isinstance(session_id, str) and session_id and isinstance(provider, str)
            )
    except sqlite3.OperationalError as exc:
        if "unable to open database file" not in str(exc).casefold():
            raise
        temp_path = _copy_sqlite_to_temp(db_path)
        try:
            return tuple(
                _SqliteThreadPlan(db_path=db_path, session_id=item.session_id, provider=item.provider)
                for item in _sqlite_thread_plans(temp_path)
            )
        finally:
            temp_path.unlink(missing_ok=True)


def _update_sqlite_provider(
    db_path: Path,
    source_provider: str,
    target_provider: str,
    *,
    expected_rows: int,
    progress_callback: ByteProgress | None = None,
) -> int:
    try:
        return _update_sqlite_provider_in_place(
            db_path,
            source_provider,
            target_provider,
            expected_rows=expected_rows,
            progress_callback=progress_callback,
        )
    except sqlite3.OperationalError as exc:
        if "unable to open database file" not in str(exc).casefold():
            raise
        # Some bind mounts reject direct SQLite writes from inside a container.
        # Updating a sibling copy and moving it back keeps the operation scoped
        # to the mounted Codex home.
        return _update_sqlite_provider_via_copy(
            db_path,
            source_provider,
            target_provider,
            expected_rows=expected_rows,
            progress_callback=progress_callback,
        )


def _update_sqlite_thread_provider(
    db_path: Path,
    session_id: str,
    source_provider: str,
    target_provider: str,
) -> int:
    try:
        return _update_sqlite_thread_provider_in_place(db_path, session_id, source_provider, target_provider)
    except sqlite3.OperationalError as exc:
        if "unable to open database file" not in str(exc).casefold():
            raise
        temp_path = _copy_sqlite_to_temp(db_path)
        try:
            updated = _update_sqlite_thread_provider_in_place(temp_path, session_id, source_provider, target_provider)
            if updated:
                _replace_sqlite_db(temp_path, db_path)
            return updated
        finally:
            temp_path.unlink(missing_ok=True)


def _update_sqlite_thread_provider_in_place(
    db_path: Path,
    session_id: str,
    source_provider: str,
    target_provider: str,
) -> int:
    with closing(_connect_sqlite(db_path)) as conn:
        if not _sqlite_has_threads_table(conn) or not _sqlite_has_model_provider_column(conn):
            return 0
        cursor = conn.execute(
            "UPDATE threads SET model_provider = ? WHERE id = ? AND model_provider = ?",
            (target_provider, session_id, source_provider),
        )
        conn.commit()
        return int(cursor.rowcount if cursor.rowcount != -1 else 0)


def _update_sqlite_provider_in_place(
    db_path: Path,
    source_provider: str,
    target_provider: str,
    *,
    expected_rows: int,
    progress_callback: ByteProgress | None = None,
) -> int:
    with closing(_connect_sqlite(db_path)) as conn:
        if not _sqlite_has_threads_table(conn) or not _sqlite_has_model_provider_column(conn):
            return 0
        updated = 0
        while updated < expected_rows:
            batch_size = min(_SQLITE_UPDATE_BATCH_SIZE, expected_rows - updated)
            cursor = conn.execute(
                """
                UPDATE threads
                SET model_provider = ?
                WHERE id IN (
                    SELECT id FROM threads WHERE model_provider = ? LIMIT ?
                )
                """,
                (target_provider, source_provider, batch_size),
            )
            batch_updated = int(cursor.rowcount if cursor.rowcount != -1 else 0)
            if batch_updated <= 0:
                break
            updated += batch_updated
            _report_progress(progress_callback, updated, expected_rows, f"Updating {db_path.name}")
        conn.commit()
        return updated


def _update_sqlite_provider_via_copy(
    db_path: Path,
    source_provider: str,
    target_provider: str,
    *,
    expected_rows: int,
    progress_callback: ByteProgress | None = None,
) -> int:
    temp_path = _copy_sqlite_to_temp(db_path)
    try:
        updated = _update_sqlite_provider_in_place(
            temp_path,
            source_provider,
            target_provider,
            expected_rows=expected_rows,
            progress_callback=progress_callback,
        )
        if updated:
            _replace_sqlite_db(temp_path, db_path)
        return updated
    finally:
        temp_path.unlink(missing_ok=True)


def _sqlite_provider_counts_via_copy(db_path: Path) -> tuple[ProviderCount, ...]:
    temp_path = _copy_sqlite_to_temp(db_path)
    try:
        return _sqlite_provider_counts(temp_path)
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
    if destination.exists():
        shutil.copymode(destination, source)
    with source.open("r+b") as source_handle:
        os.fsync(source_handle.fileno())
    os.replace(source, destination)
    for sidecar in _sqlite_sidecar_paths(destination):
        sidecar.unlink(missing_ok=True)


def _connect_sqlite(db_path: Path, *, read_only: bool = False, immutable: bool = False) -> sqlite3.Connection:
    target = str(db_path)
    if read_only:
        quoted_path = _quote_sqlite_uri_path(str(db_path.resolve()))
        immutable_flag = "&immutable=1" if immutable else ""
        target = f"file:{quoted_path}?mode=ro{immutable_flag}"
    conn = sqlite3.connect(target, timeout=5, uri=read_only)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
    except Exception:
        conn.close()
        raise
    return conn


def _quote_sqlite_uri_path(path_text: str) -> str:
    return quote(path_text.replace("\\", "/"), safe="/:")


def _sqlite_has_threads_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'threads'",
    ).fetchone()
    return row is not None


def _sqlite_has_model_provider_column(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA table_info(threads)").fetchall()
    return any(row[1] == "model_provider" for row in rows)


def _verify_retag_plan(
    plan: _RetagPlan,
    source_provider: str,
    target_provider: str,
    progress_callback: ByteProgress | None = None,
) -> None:
    total_items = len(plan.jsonl_targets) + len(plan.sqlite_targets)
    completed_items = 0
    _report_progress(progress_callback, completed_items, total_items, "Verifying changed metadata")
    for target in plan.jsonl_targets:
        observed = _discover_jsonl_session(target.path)
        if observed.provider != target_provider:
            raise RuntimeError(
                f"JSONL verification failed for {target.path}: expected {target_provider}, "
                f"found {observed.provider or 'no provider'}"
            )
        completed_items += 1
        _report_progress(progress_callback, completed_items, total_items, f"Verified {target.path.name}")

    for target in plan.sqlite_targets:
        observed_counts = _sqlite_provider_counts(target.path)
        expected_moved = _provider_count(target.provider_counts, source_provider)
        expected_target = _provider_count(target.provider_counts, target_provider) + expected_moved
        observed_source = _provider_count(observed_counts, source_provider)
        observed_target = _provider_count(observed_counts, target_provider)
        if observed_source != 0 or observed_target != expected_target:
            raise RuntimeError(
                f"SQLite verification failed for {target.path}: source={observed_source}, "
                f"target={observed_target}, expected_target={expected_target}"
            )
        completed_items += 1
        _report_progress(progress_callback, completed_items, total_items, f"Verified {target.path.name}")


def _verify_targeted_metadata_repair(
    codex_home: Path,
    active_provider: str,
    session_ids: Sequence[str],
    jsonl_targets: Sequence[_JsonlSessionPlan],
    sqlite_targets: Sequence[_SqliteThreadPlan],
) -> None:
    for target in jsonl_targets:
        observed = _discover_jsonl_session(target.path)
        if observed.session_id != target.session_id or observed.provider != active_provider:
            raise RuntimeError(f"JSONL verification failed for targeted session {target.session_id} in {target.path}")
    for target in sqlite_targets:
        observed = tuple(item for item in _sqlite_thread_plans(target.db_path) if item.session_id == target.session_id)
        if len(observed) != 1 or observed[0].provider != active_provider:
            raise RuntimeError(
                f"SQLite verification failed for targeted session {target.session_id} in {target.db_path}"
            )
    remaining = preview_session_metadata_mismatches(codex_home=codex_home, active_provider=active_provider)
    remaining_ids = {item.session_id for item in remaining.mismatches}
    unexpected = sorted(set(session_ids) & remaining_ids)
    if unexpected:
        raise RuntimeError("targeted metadata verification found remaining mismatches: " + ", ".join(unexpected))


def _restore_backup(codex_home: Path, backup_path: Path) -> None:
    for source in backup_path.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(backup_path)
        if relative.parts and relative.parts[0] == "sessions":
            destination = codex_home / relative
        elif len(relative.parts) == 1 and relative.name.startswith("state_") and relative.suffix == ".sqlite":
            destination = codex_home / relative
        else:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_file_atomically(source, destination)
        if destination.suffix == ".sqlite":
            for sidecar in _sqlite_sidecar_paths(destination):
                sidecar.unlink(missing_ok=True)


def _copy_file_atomically(source: Path, destination: Path) -> None:
    with NamedTemporaryFile("wb", dir=destination.parent, delete=False) as output_handle:
        temporary = Path(output_handle.name)
        try:
            with source.open("rb") as input_handle:
                while chunk := input_handle.read(_COPY_CHUNK_SIZE):
                    output_handle.write(chunk)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    shutil.copystat(source, temporary)
    os.replace(temporary, destination)


def _derive_provider_counts_after(
    before: tuple[ProviderCount, ...],
    source_provider: str,
    target_provider: str,
    moved_count: int,
) -> tuple[ProviderCount, ...]:
    counts = {provider_count.provider: provider_count.count for provider_count in before}
    source_count = counts.get(source_provider, 0)
    if moved_count > source_count:
        raise RuntimeError(
            f"retag plan moves {moved_count} records but only {source_count} source records were discovered"
        )
    remaining_source = source_count - moved_count
    if remaining_source:
        counts[source_provider] = remaining_source
    else:
        counts.pop(source_provider, None)
    counts[target_provider] = counts.get(target_provider, 0) + moved_count
    return _provider_counts_tuple(counts)


def _opposite_provider(provider: str) -> str:
    return "codex-lb" if provider == "openai" else "openai"


def _provider_count(provider_counts: Sequence[ProviderCount], provider: str) -> int:
    return next((item.count for item in provider_counts if item.provider == provider), 0)


def _provider_counts_tuple(counts: dict[str, int]) -> tuple[ProviderCount, ...]:
    return tuple(ProviderCount(provider, count) for provider, count in sorted(counts.items()) if count > 0)


def _report_progress(
    progress_callback: ByteProgress | None,
    completed: int,
    total: int,
    message: str,
) -> None:
    if progress_callback is not None:
        progress_callback(completed, total, message)


def _emit_structured_progress(
    progress_logger: ProgressLogger | None,
    phase: str,
    completed: int,
    total: int,
    unit: str,
    message: str,
) -> None:
    if progress_logger is None:
        return
    payload = {
        "phase": phase,
        "completed": max(0, completed),
        "total": max(0, total),
        "unit": unit,
        "message": message,
    }
    progress_logger(PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _methods_used(jsonl_files: Sequence[Path], state_dbs: Sequence[Path]) -> tuple[str, ...]:
    methods: list[str] = []
    if jsonl_files:
        methods.append("jsonl")
    if state_dbs:
        methods.append("sqlite")
    return tuple(methods)


def _create_backup(
    codex_home: Path,
    jsonl_files: Sequence[Path],
    state_dbs: Sequence[Path],
    *,
    progress_callback: ByteProgress | None = None,
) -> Path:
    backup_dir = _next_backup_dir(codex_home / "backups" / PROVIDER_RETAG_BACKUP_DIR)
    backup_dir.mkdir(parents=True)

    session_index = codex_home / "session_index.jsonl"
    backup_sources = tuple(state_dbs) + ((session_index,) if session_index.is_file() else ()) + tuple(jsonl_files)
    total_bytes = sum(path.stat().st_size for path in backup_sources)
    completed_bytes = 0
    _report_progress(progress_callback, completed_bytes, total_bytes, "Starting metadata backup")

    for db_path in state_dbs:
        source_size = db_path.stat().st_size
        base_completed = completed_bytes
        _backup_sqlite_db(
            db_path,
            backup_dir / db_path.name,
            progress_callback=lambda copied, total, name=db_path.name, base=base_completed, size=source_size: (
                _report_progress(
                    progress_callback,
                    min(base + _scale_progress(copied, total, size), base + size),
                    total_bytes,
                    f"Backing up {name}",
                )
            ),
        )
        completed_bytes += source_size
        _report_progress(progress_callback, completed_bytes, total_bytes, f"Backed up {db_path.name}")

    if session_index.is_file():
        source_size = session_index.stat().st_size
        base_completed = completed_bytes
        _copy_file_safely(
            session_index,
            backup_dir / session_index.name,
            progress_callback=lambda copied, _total, message, base=base_completed: _report_progress(
                progress_callback,
                base + copied,
                total_bytes,
                message,
            ),
        )
        completed_bytes += source_size

    for path in jsonl_files:
        destination = backup_dir / path.relative_to(codex_home)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_size = path.stat().st_size
        base_completed = completed_bytes
        _backup_jsonl_file(
            path,
            destination,
            progress_callback=lambda copied, _total, message, base=base_completed: _report_progress(
                progress_callback,
                base + copied,
                total_bytes,
                message,
            ),
        )
        completed_bytes += source_size
        _report_progress(progress_callback, completed_bytes, total_bytes, f"Backed up {path.name}")

    _report_progress(progress_callback, total_bytes, total_bytes, "Metadata backup complete")
    return backup_dir


def _backup_jsonl_file(
    source: Path,
    destination: Path,
    *,
    progress_callback: ByteProgress | None = None,
) -> None:
    source_size = source.stat().st_size
    try:
        os.link(source, destination)
    except OSError:
        destination.unlink(missing_ok=True)
        _copy_file_safely(source, destination, progress_callback=progress_callback)
        return
    if destination.stat().st_size != source_size:
        destination.unlink(missing_ok=True)
        raise OSError(f"hard-link backup size mismatch for {source}")
    _report_progress(progress_callback, source_size, source_size, f"Hard-linked {source.name}")


def _copy_file_safely(
    source: Path,
    destination: Path,
    *,
    progress_callback: ByteProgress | None = None,
) -> None:
    source_size = source.stat().st_size
    copied = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as input_handle, destination.open("xb") as output_handle:
            while chunk := input_handle.read(_COPY_CHUNK_SIZE):
                output_handle.write(chunk)
                copied += len(chunk)
                _report_progress(progress_callback, copied, source_size, f"Copying {source.name}")
            output_handle.flush()
            os.fsync(output_handle.fileno())
        shutil.copystat(source, destination)
        if destination.stat().st_size != source_size:
            raise OSError(f"copy backup size mismatch for {source}")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    _report_progress(progress_callback, source_size, source_size, f"Copied {source.name}")


def _backup_sqlite_db(
    source: Path,
    destination: Path,
    *,
    progress_callback: ByteProgress | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(_connect_sqlite(source, read_only=True)) as source_conn,
        closing(sqlite3.connect(str(destination))) as backup_conn,
    ):
        page_size_row = source_conn.execute("PRAGMA page_size").fetchone()
        page_size = int(page_size_row[0]) if page_size_row is not None else 4096

        def report_backup(_status: int, remaining: int, total: int) -> None:
            _report_progress(
                progress_callback,
                max(0, total - remaining) * page_size,
                max(0, total) * page_size,
                f"Backing up {source.name}",
            )

        source_conn.backup(backup_conn, pages=256, progress=report_backup)
        backup_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        backup_conn.execute("PRAGMA journal_mode=DELETE")
        backup_conn.commit()
    for sidecar in _sqlite_sidecar_paths(destination):
        sidecar.unlink(missing_ok=True)
    destination_size = destination.stat().st_size
    _report_progress(progress_callback, destination_size, destination_size, f"Backed up {source.name}")


def _scale_progress(completed: int, total: int, output_total: int) -> int:
    if total <= 0:
        return output_total
    return min(output_total, int(output_total * completed / total))


def _consolidate_sqlite_db(db_path: Path) -> None:
    with closing(_connect_sqlite(db_path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.commit()


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
