#!/bin/sh
set -eu

if [ "${CODEX_LB_DATABASE_MIGRATE_ON_STARTUP:-true}" = "true" ]; then
  python -m app.db.migrate upgrade
fi

# Disable app-level startup migration so app/db/session.py init_db() does not
# run migrations again inside the app process.
export CODEX_LB_DATABASE_MIGRATE_ON_STARTUP=false

host="${CODEX_LB_HOST:-${HOST:-0.0.0.0}}"
port="${PORT:-2455}"

exec python -m app.cli --host "$host" --port "$port"
