## ADDED Requirements

### Requirement: Shipped container images share startup migration supervision

The standard and distroless container entrypoints MUST use the same Python startup-migration supervisor when migration is enabled. The standard image MUST delegate to that supervisor when `CODEX_LB_DATABASE_MIGRATE_ON_STARTUP` is unset or exactly `true`, and MUST bypass migration and delegate directly to the project CLI when the value is exactly `false`. The distroless image MUST preserve its existing always-migrate behavior regardless of the incoming value.

Every successful handoff MUST set `CODEX_LB_DATABASE_MIGRATE_ON_STARTUP=false` before replacing the entrypoint process with `python -m app.cli --host 0.0.0.0 --port 2455`. The Dockerfile CMDs MUST remain delegated to their shipped entrypoint paths.

#### Scenario: Standard image runs startup migration

- **WHEN** the standard image starts with `CODEX_LB_DATABASE_MIGRATE_ON_STARTUP` unset or exactly `true`
- **THEN** its shell entrypoint replaces itself with the shared Python migration supervisor before a migration child is started
- **AND** successful migration hands off to the project CLI with application-level migration disabled

#### Scenario: Standard image bypasses startup migration

- **WHEN** the standard image starts with `CODEX_LB_DATABASE_MIGRATE_ON_STARTUP=false`
- **THEN** its shell entrypoint does not start a migration child
- **AND** it replaces itself directly with the project CLI while preserving the environment value `false`

#### Scenario: Distroless image preserves always-migrate behavior

- **WHEN** the distroless image starts with any incoming `CODEX_LB_DATABASE_MIGRATE_ON_STARTUP` value
- **THEN** it runs the shared startup-migration supervisor
- **AND** successful migration hands off to the project CLI with application-level migration disabled

#### Scenario: Container application handoff stays canonical

- **WHEN** either shipped image completes its required startup-migration decision successfully
- **THEN** the entrypoint process is replaced by `python -m app.cli --host 0.0.0.0 --port 2455`
- **AND** no direct Uvicorn launcher or alternate application command is introduced
