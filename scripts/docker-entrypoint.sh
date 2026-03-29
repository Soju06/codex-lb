#!/bin/sh
set -eu

if [ "${CODEX_LB_DATABASE_MIGRATE_ON_STARTUP:-false}" = "true" ]; then
  python -m app.db.migrate upgrade
fi

# Disable app-level startup migration so app/db/session.py init_db() does not
# run migrations again inside the app process.
export CODEX_LB_DATABASE_MIGRATE_ON_STARTUP=false

exec python -m app.cli --host 0.0.0.0 --port 2455
