# Deployment Installation Context

## Purpose and Scope

This capability explains how operators choose a deployment shape before applying the normative installation requirements in `spec.md`. It covers Docker, production Compose, and Helm guidance without replacing the specific commands in the README and chart README.

## Decisions

- Keep the zero-config quick start SQLite-first for local evaluation and smoke usage.
- Use PostgreSQL as the greenfield baseline for new sustained deployments before increasing request-worker concurrency.
- Preserve the standard `2455` API port and `1455` OAuth callback port across Docker, Compose, and Helm examples.
- Preserve the operator's selected Codex CLI service tier. The greenfield baseline verifies requested versus actual upstream served tier instead of recommending an automatic downgrade or replacement.
- When the HTTP Responses session bridge is enabled, multi-worker serving should use the addressable bridge worker pool from the packaged entrypoint or `python -m app.cli`. Plain Uvicorn multi-worker serving belongs only to deployments that explicitly disable the bridge.

## Failure Modes

- A deployment can pass `/health/ready` while SQLite write contention hurts request logging, usage settlement, or bridge ownership writes. Treat sustained lock pressure as a signal to move to PostgreSQL.
- Plain multi-worker Uvicorn with the bridge enabled can split in-memory session ownership and lose Responses continuity.
- Upstream can return an actual service tier that differs from the requested tier. That is an operational signal to measure, not a reason for codex-lb to mutate the request.
- Metrics enabled without scrape or alerting still leaves operators blind to slow-tail latency and upstream failure bursts.

## Operational Example

For a new always-on gateway:

```bash
CODEX_LB_DATABASE_URL=postgresql+asyncpg://codex_lb:codex_lb@db.example.com:5432/codex_lb
CODEX_LB_UVICORN_WORKERS=2
CODEX_LB_METRICS_ENABLED=true
CODEX_LB_METRICS_PORT=9090
```

Run through the packaged entrypoint, production Compose file, or Helm chart so the bridge worker-pool and metrics surfaces are wired consistently. Keep SQLite with `CODEX_LB_UVICORN_WORKERS=1` for local smoke usage.
