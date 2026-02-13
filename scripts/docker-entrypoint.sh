#!/bin/sh
set -eu

python -m app.db.migrate upgrade

export CODEX_LB_DATABASE_MIGRATE_ON_STARTUP=false
exec fastapi run --host 0.0.0.0 --port 2455
