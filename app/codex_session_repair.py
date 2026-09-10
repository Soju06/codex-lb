"""Apply one fresh, session-scoped metadata plan with retained recovery evidence."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp

from app.codex_session_metadata import (
    SUPPORTED_PROVIDERS,
    JsonlMetadata,
    MetadataPlan,
    Progress,
    discover_metadata,
    file_identity,
    metadata_payload,
    read_metadata,
    validate_provider,
)
from app.codex_sessions_retag import _backup_sqlite_db, _connect_sqlite


def repair_metadata(home: Path, provider: str, session_ids: Sequence[str], progress: Progress) -> Path | None:
    validate_provider(provider)
    if not session_ids:
        raise ValueError("At least one session ID is required")
    plan = discover_metadata(home, progress).select(session_ids)
    if any(tags - SUPPORTED_PROVIDERS for tags in plan.providers().values()):
        raise ValueError("Selected sessions contain unsupported provider tags")
    changed = MetadataPlan(
        tuple(item for item in plan.jsonl if item.provider != provider),
        tuple(item for item in plan.rows if item.provider != provider),
    )
    if not changed.jsonl and not changed.rows:
        _check_plan(plan)
        _verify(plan, provider, progress)
        return None
    _check_plan(plan)
    backup_root = home / "backups" / "session-metadata-repair"
    if not backup_root.resolve().is_relative_to(home) or any(
        path.is_symlink() for path in (home / "backups", backup_root)
    ):
        raise ValueError("Unsafe backup directory; symlinks are not supported")
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = Path(mkdtemp(prefix="repair-", dir=backup_root))
    try:
        _backup(changed, home, backup, progress)
        _check_plan(plan)
        progress("rewrite", 0)
        for item in changed.jsonl:
            _rewrite(item, provider, progress)
        progress("sqlite", 0)
        for path in sorted({item.path for item in changed.rows}):
            with closing(_connect_sqlite(path)) as conn, conn:
                for item in changed.rows:
                    if item.path != path:
                        continue
                    if path.is_symlink() or file_identity(path)[:2] != item.identity:
                        raise ValueError(f"State database changed before update: {path}")
                    cursor = conn.execute(
                        "UPDATE threads SET model_provider = ? WHERE id = ? AND model_provider = ?",
                        (provider, item.session_id, item.provider),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError(f"Session changed before SQLite update: {item.session_id}")
                    progress("sqlite", 1)
        _verify(plan, provider, progress)
    except Exception as exc:
        raise ValueError(f"Repair failed; retained backup at {backup}: {exc}") from exc
    return backup


def _check_plan(plan: MetadataPlan) -> None:
    for item in plan.jsonl:
        if item.path.is_symlink() or file_identity(item.path) != item.identity:
            raise ValueError(f"Session changed since discovery: {item.path}")
    for path in sorted({item.path for item in plan.rows}):
        if path.is_symlink():
            raise ValueError(f"State database changed since discovery: {path}")
        with closing(_connect_sqlite(path, read_only=True)) as conn:
            for item in plan.rows:
                if item.path == path:
                    if file_identity(path)[:2] != item.identity:
                        raise ValueError(f"State database changed since discovery: {path}")
                    rows = conn.execute(
                        "SELECT model_provider FROM threads WHERE id = ?", (item.session_id,)
                    ).fetchall()
                    if rows != [(item.provider,)]:
                        raise ValueError(f"Session changed since discovery: {item.session_id}")


def _backup(plan: MetadataPlan, home: Path, backup: Path, progress: Progress) -> None:
    progress("backup", 0)
    for item in plan.jsonl:
        destination = backup / item.path.relative_to(home)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(item.path, destination)
        except OSError:
            shutil.copy2(item.path, destination)
        progress("backup", 1)
    for path in sorted({item.path for item in plan.rows}):
        _backup_sqlite_db(path, backup / path.name)
        progress("backup", 1)


def _rewrite(item: JsonlMetadata, provider: str, progress: Progress) -> None:
    record, payload = metadata_payload(item.header)
    payload["model_provider"] = provider
    newline = b"\r\n" if item.header.endswith(b"\r\n") else b"\n" if item.header.endswith(b"\n") else b""
    temp: Path | None = None
    try:
        with item.path.open("rb") as source, NamedTemporaryFile("wb", dir=item.path.parent, delete=False) as target:
            temp = Path(target.name)
            if source.read(len(item.header)) != item.header:
                raise ValueError(f"Session metadata changed: {item.path}")
            target.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode() + newline)
            copied = 0
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
                copied += len(chunk)
                progress("rewrite", copied)
            target.flush()
            os.fsync(target.fileno())
        if item.path.is_symlink() or file_identity(item.path) != item.identity:
            raise ValueError(f"Session changed before replacement: {item.path}")
        shutil.copymode(item.path, temp)
        temp.replace(item.path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _verify(plan: MetadataPlan, provider: str, progress: Progress) -> None:
    progress("verification", 0)
    for item in plan.jsonl:
        current = read_metadata(item.path)
        if current is None or current.session_id != item.session_id or current.provider != provider:
            raise ValueError(f"Session verification failed: {item.path}")
        progress("verification", 1)
    for path in sorted({item.path for item in plan.rows}):
        with closing(_connect_sqlite(path, read_only=True)) as conn:
            for item in plan.rows:
                if item.path == path:
                    rows = conn.execute(
                        "SELECT model_provider FROM threads WHERE id = ?", (item.session_id,)
                    ).fetchall()
                    if rows != [(provider,)]:
                        raise ValueError(f"SQLite verification failed: {item.session_id}")
                    progress("verification", 1)
