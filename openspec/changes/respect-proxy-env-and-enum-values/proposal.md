## Why

This deployment runs codex-lb behind an explicit local proxy chain inside WSL. Outbound HTTP clients currently ignore `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`, which causes GitHub release checks and upstream OAuth/proxy calls to bypass the configured network path.

The ORM enum declarations also rely on SQLAlchemy defaults that use enum member names instead of the lowercase string values already used by the PostgreSQL schema and Alembic migrations. That mismatch risks runtime failures and schema drift when PostgreSQL is enabled.

## What Changes

- Make shared outbound `aiohttp` clients honor environment proxy settings via `trust_env=True`.
- Align ORM enum persistence with the lowercase string values already defined in the database schema.
- Add regression tests for both behaviors.

## Impact

- Code: `app/core/clients/http.py`, `app/core/clients/codex_version.py`, `app/db/models.py`
- Tests: `tests/unit/test_http_client.py`, `tests/unit/test_codex_version.py`, `tests/unit/test_db_models.py`
- Specs: `openspec/specs/outbound-http-clients/spec.md`, `openspec/specs/database-backends/spec.md`
