from __future__ import annotations

import base64
import io
import os
import sys
import zipfile
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import unquote

STORE_ARCHIVE_PREFIX = "CODEX_LB_VOLUME_IMPORT_STORE_ZIP_B64_"
ENCRYPTION_KEY_VAR = "CODEX_LB_VOLUME_IMPORT_ENCRYPTION_KEY_B64"
FORCE_VAR = "CODEX_LB_VOLUME_IMPORT_FORCE"
DEFAULT_ENCRYPTION_KEY_PATH = Path("/var/lib/codex-lb/encryption.key")


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _sqlite_database_path() -> Path:
    database_url = os.environ.get("CODEX_LB_DATABASE_URL")
    if not database_url:
        return Path("/var/lib/codex-lb/store.db")
    if database_url.startswith("sqlite+aiosqlite:///"):
        return Path(unquote(database_url.removeprefix("sqlite+aiosqlite:///")))
    if database_url.startswith("sqlite:///"):
        return Path(unquote(database_url.removeprefix("sqlite:///")))
    raise RuntimeError("CODEX_LB_VOLUME_IMPORT_STORE_ZIP_B64_* requires CODEX_LB_DATABASE_URL to be a sqlite URL")


def _write_bytes(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, delete=False) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(payload)
    temp_path.replace(target)


def _decode_store_archive() -> bytes | None:
    chunk_items = sorted(
        (key, value) for key, value in os.environ.items() if key.startswith(STORE_ARCHIVE_PREFIX) and value
    )
    if not chunk_items:
        return None
    joined_payload = "".join(value for _, value in chunk_items)
    archive_bytes = base64.b64decode(joined_payload)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        members = [member for member in archive.infolist() if not member.is_dir()]
        if len(members) != 1:
            raise RuntimeError("store import archive must contain exactly one file")
        return archive.read(members[0])


def _remove_sqlite_sidecars(database_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = database_path.with_name(f"{database_path.name}{suffix}")
        if sidecar.exists():
            sidecar.unlink()


def _import_store(force: bool) -> None:
    store_bytes = _decode_store_archive()
    if store_bytes is None:
        return

    database_path = _sqlite_database_path()
    if database_path.exists() and not force:
        print(f"store import skipped because {database_path} already exists", file=sys.stderr)
        return

    _remove_sqlite_sidecars(database_path)
    _write_bytes(database_path, store_bytes)
    print(f"restored sqlite store to {database_path}", file=sys.stderr)


def _import_encryption_key(force: bool) -> None:
    encoded_key = os.environ.get(ENCRYPTION_KEY_VAR)
    if not encoded_key:
        return

    target = Path(os.environ.get("CODEX_LB_ENCRYPTION_KEY_FILE") or DEFAULT_ENCRYPTION_KEY_PATH)
    if target.exists() and not force:
        print(f"encryption key import skipped because {target} already exists", file=sys.stderr)
        return

    _write_bytes(target, base64.b64decode(encoded_key))
    print(f"restored encryption key to {target}", file=sys.stderr)


def main() -> int:
    force = _is_truthy(os.environ.get(FORCE_VAR))
    _import_store(force)
    _import_encryption_key(force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
