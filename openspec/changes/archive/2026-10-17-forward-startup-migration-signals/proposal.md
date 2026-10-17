## Why

Both shipped container entrypoints wait for a startup-migration child before the project CLI owns process signals. Container runtimes signal PID 1 rather than forwarding to that child, so a stop or replacement during migration can bypass orderly migration termination and child reaping.

## What Changes

- Make the Python container entrypoint supervise the startup-migration child, forward SIGTERM and SIGINT, and reap the child before exiting or launching the application.
- Route the standard image's migration-enabled shell path through the same Python supervisor while preserving its migration-disabled direct application handoff.
- Preserve successful migration, fail-fast migration errors, the distroless always-migrate contract, and the existing `app.cli` command.
- Add deterministic process-level regressions for signal forwarding, child reaping, interrupted-start suppression, successful startup, and migration-disabled startup.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: Define signal ownership, child reaping, and fail-fast application suppression for container startup migrations.
- `deployment-installation`: Define how the standard and distroless launch paths delegate migration supervision and preserve the owned application CLI handoff.

## Impact

This change affects `scripts/docker-entrypoint.sh`, `scripts/distroless-entrypoint.py`, their launcher contract tests, and new isolated process tests. It does not change migration SQL, Alembic behavior, database settings, Docker stop grace, Helm, Compose, dependencies, or public APIs.
