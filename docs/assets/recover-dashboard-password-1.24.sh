#!/usr/bin/env bash
# Docker recovery for codex-lb 1.24.0 with SQLite. This password change does
# not revoke existing dashboard cookies; they retain their embedded expiry.
set -euo pipefail

container=codex-lb
if [[ ! -t 0 ]]; then
  echo 'Run this script from an interactive terminal.' >&2
  exit 1
fi

backup_dir=$(mktemp -d "$PWD/codex-lb-reset-backup.XXXXXX")
container_backup="/tmp/$(basename "$backup_dir").db"

backup_code=$(cat <<'PY'
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from app import __version__
from app.core.config.settings import get_settings
from app.db.sqlite_utils import sqlite_db_path_from_url

if __version__ != "1.24.0":
    raise SystemExit(f"Expected codex-lb 1.24.0; found {__version__}")
source = sqlite_db_path_from_url(get_settings().database_url)
target = Path(sys.argv[1])
if source is None or not source.is_file() or target.exists():
    raise SystemExit("SQLite source missing or backup target already exists")
with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
    with closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise SystemExit("Backup integrity check failed")
target.chmod(0o600)
PY
)

echo 'Creating a consistent SQLite backup...'
docker exec -w /app "$container" python -c "$backup_code" "$container_backup"
docker cp "$container:$container_backup" "$backup_dir/store.db"
chmod 600 "$backup_dir/store.db"
if [[ ! -s "$backup_dir/store.db" ]]; then
  echo 'Backup copy is empty; password was not changed.' >&2
  exit 1
fi
docker exec "$container" python -c 'from pathlib import Path; import sys; Path(sys.argv[1]).unlink()' "$container_backup"
echo "Backup saved: $backup_dir/store.db"

reset_code=$(cat <<'PY'
import asyncio
import getpass

from app import __version__
from app.db.session import SessionLocal
from app.modules.dashboard_auth.repository import DashboardAuthRepository
from app.modules.dashboard_auth.service import _hash_password

if __version__ != "1.24.0":
    raise SystemExit(f"Expected codex-lb 1.24.0; found {__version__}")
password = getpass.getpass("New admin password: ")
if password != getpass.getpass("Repeat new password: "):
    raise SystemExit("Passwords do not match")
if not 8 <= len(password.encode("utf-8")) <= 72:
    raise SystemExit("Use 8 to 72 UTF-8 bytes")

async def reset():
    async with SessionLocal() as session:
        repository = DashboardAuthRepository(session)
        if await repository.get_password_hash() is None:
            raise SystemExit("No existing dashboard password; stopped")
        await repository.set_password_hash(_hash_password(password))

asyncio.run(reset())
print("Dashboard password updated")
PY
)

docker exec -it -w /app "$container" python -c "$reset_code"
docker restart "$container" >/dev/null
echo 'Container restarted. Sign in with the new password.'
echo "Backup: $backup_dir/store.db"
