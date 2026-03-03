## Why

The project currently defaults to SQLite and has no documented, validated path for running against PostgreSQL. For higher concurrency and more resilient deployments, we need a supported PostgreSQL integration path without breaking the existing zero-config SQLite default.

## What Changes

- Add PostgreSQL async driver dependency and lockfile updates.
- Keep SQLite as the default backend and preserve existing SQLite-first local behavior.
- Add optional PostgreSQL configuration examples.
- Make test setup backend-agnostic so CI can run against SQLite and PostgreSQL.
- Add a dedicated CI test job that runs the suite with PostgreSQL.

## Capabilities

### New Capabilities
- `database-backends`: Define supported database backends and default backend behavior.

### Modified Capabilities
- `none`: No existing capability specs currently define database backend behavior.

## Impact

- Runtime deps: `pyproject.toml`, `uv.lock`
- Config/docs: `.env.example`, `README.md`
- Tests: `tests/conftest.py`, `tests/unit/test_db_session.py`
- CI: `.github/workflows/ci.yml`
- Specs: `openspec/specs/database-backends/spec.md`, `openspec/specs/database-backends/context.md`
