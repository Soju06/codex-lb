## Context

codex-lb currently supports SQLite and PostgreSQL through CODEX_LB_DATABASE_URL. SQLite is the zero-config default and remains the right path for a single local container, especially when the runtime stays on one request worker. The live direct-container profile follows that conservative shape: SQLite storage, standard ports, and one Uvicorn worker.

The next throughput limit appears when operators combine sustained concurrent Codex traffic, write-heavy request logging, dashboard/auth state, bridge/session metadata, and multiple runtime workers. SQLite's single-writer behavior can still be hardened with retries, but it should not be presented as the preferred backend for high-concurrency serving. PostgreSQL is already the supported backend that removes that deployment-class bottleneck without changing API contracts.

## Goals / Non-Goals

**Goals:**

- Keep the Docker quick start SQLite-first and zero-config.
- Make PostgreSQL the documented recommendation for higher-concurrency Docker and infrastructure-managed deployments.
- Provide an operator path that uses CODEX_LB_DATABASE_URL and preserves standard service ports 2455 and 1455.
- Make the guidance compatible with existing worker and bridge-pool runtime tuning.

**Non-Goals:**

- Do not migrate existing SQLite deployments automatically.
- Do not require PostgreSQL for basic local startup.
- Do not add a second database configuration key or alias.
- Do not change OAuth callback ports, OpenAI-compatible API routes, or dashboard auth behavior.
- Do not prescribe a single managed PostgreSQL provider.

## Decisions

### Preserve SQLite as the convenience default

The default Docker and local startup path should continue to require no external services. If CODEX_LB_DATABASE_URL is unset, runtime behavior stays SQLite-backed.

Alternative considered: require PostgreSQL for all Docker installs. This would simplify performance guidance but would make the first-run path heavier and break the current zero-config promise.

### Recommend PostgreSQL for concurrency rather than raising SQLite workers

Official guidance should say that higher-concurrency, sustained multi-worker, or write-heavy deployments should use PostgreSQL. SQLite can remain supported for low-concurrency and development use, but worker increases on SQLite should not be framed as the primary scaling path.

Alternative considered: keep tuning SQLite retry/backoff and present it as the main performance profile. Retries reduce transient lock failures but do not remove SQLite's single-writer ceiling.

### Use CODEX_LB_DATABASE_URL as the only database selection interface

The performance profile should configure PostgreSQL with the existing CODEX_LB_DATABASE_URL=postgresql+asyncpg://... interface. This keeps runtime, Docker, Compose, and Helm behavior aligned.

Alternative considered: add a CODEX_LB_DATABASE_BACKEND=postgres convenience flag. That would create duplicate configuration state and force extra precedence rules.

### Keep listener and OAuth callback ports stable

The PostgreSQL-backed Docker profile should keep app port 2455 and OAuth callback port 1455. Storage selection should not require clients, OpenClaw, OpenCode, Codex CLI, or OAuth callback configuration to change.

Alternative considered: publish a separate high-throughput port layout. That adds operator churn without solving the database bottleneck.

## Risks / Trade-offs

- PostgreSQL raises setup complexity -> keep SQLite as the default quick start and provide PostgreSQL as an explicit performance profile.
- Operators may raise worker count while still on SQLite -> document that PostgreSQL is the recommended backend before sustained multi-worker serving.
- Existing SQLite users may read the recommendation as a deprecation -> state that SQLite remains supported for zero-config and simple local startup.
- Database URL examples can accidentally encourage secret leakage -> use placeholders and keep credentials out of committed local env files.
- PostgreSQL availability becomes part of runtime readiness -> rely on existing readiness checks and fail closed when the configured database cannot connect.

## Migration Plan

1. Update the normative specs to preserve SQLite defaults and recommend PostgreSQL for higher-concurrency deployments.
2. Update README and environment examples with a PostgreSQL-backed Docker performance profile using CODEX_LB_DATABASE_URL.
3. Keep the direct Docker helper ports unchanged.
4. Validate OpenSpec artifacts and run focused documentation/config tests if implementation changes touch examples or helper scripts.

Rollback is documentation/config only for this change: remove the PostgreSQL performance-profile guidance or revert the example updates. Existing SQLite runtime behavior remains unchanged.

## Open Questions

- Should the Docker Compose example include a bundled PostgreSQL service, or should it only show an external PostgreSQL URL to avoid implying a production database lifecycle?
- Should the direct Docker helper warn when CODEX_LB_UVICORN_WORKERS is greater than one while CODEX_LB_DATABASE_URL points at SQLite?
- Should future dashboard health expose the active database backend and worker profile so operators can spot mismatched concurrency settings faster?
