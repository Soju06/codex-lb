# prefer-postgres-for-concurrent-deployments

## Why

`codex-lb` already supports PostgreSQL, but the normative specs still treat SQLite as the only clearly documented default path for local and Docker usage. After runtime worker tuning, SQLite-backed Docker deployments become the next likely bottleneck for concurrent Codex traffic because write-heavy request logging, auth state, and bridge/session metadata still share a single-file database path.

Operators need a spec-backed deployment contract that keeps SQLite as the zero-config default while explicitly identifying PostgreSQL as the recommended backend for higher-concurrency Docker and infrastructure-managed deployments.

## What Changes

- add a `database-backends` requirement that preserves SQLite as the default backend while recommending PostgreSQL for higher-concurrency deployments
- add a `deployment-installation` requirement for a documented PostgreSQL-backed Docker performance profile
- keep the current quick-start SQLite flow unchanged for simple local startup

## Impact

- Specs: `database-backends`, `deployment-installation`
- Future docs/config work: `README.md`, `.env.example`, `docker-compose.yml`
- Runtime behavior: no immediate code-path change; this change defines the supported performance deployment guidance
