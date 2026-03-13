#!/bin/sh
set -eu

if [ -f /app/.env ]; then
  set -a
  . /app/.env
  set +a
fi

python -m app.db.migrate upgrade

export CODEX_LB_DATABASE_MIGRATE_ON_STARTUP=false
exec python -m app.cli --host 0.0.0.0 --port "${PORT:-2455}"
