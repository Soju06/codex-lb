## Why

Runtime startup migrations currently stop at `upgrade head`. If `alembic_version` says `head` but the physical schema is still drifted, the service can start and only fail later when background tasks or request paths touch the missing table/index/constraint.

That makes database drift operationally noisy and late-detected. We want startup to verify the post-migration schema contract immediately so drift is caught before the service begins normal work.

## What Changes

- run a startup schema drift check immediately after successful Alembic startup migrations
- fail startup when drift remains and `database_migrations_fail_fast=true`
- keep the existing config semantics by logging explicit drift details and continuing only when `database_migrations_fail_fast=false`
- add regression coverage for both fail-fast and continue-with-log behavior

## Impact

- Code: `app/db/session.py`
- Tests: `tests/unit/test_db_session.py`
- Specs: `openspec/specs/database-migrations/spec.md`, `openspec/specs/database-migrations/context.md`
