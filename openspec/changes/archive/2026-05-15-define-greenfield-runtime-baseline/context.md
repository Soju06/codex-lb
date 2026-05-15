# Greenfield Runtime Baseline Context

## Purpose

This change records the greenfield baseline for a codex-lb instance that is expected to serve real sustained traffic. It intentionally avoids a separate taxonomy: new serious deployments should start from the PostgreSQL, bridge-safe, observable baseline. SQLite remains the local/smoke path.

## Scope

The greenfield baseline covers:

- requested-vs-actual service-tier verification
- database backend and worker-count guidance
- bridge-safe multi-worker serving
- metrics and alerts for sustained traffic

It does not cover daily prompt shaping, default reasoning-effort policy, output-token budgets, or model-selection policy.

## Operational Example

An operator running a personal local smoke instance can keep SQLite and one request worker. An operator setting up codex-lb as a new always-on gateway for Codex/OpenClaw/Honcho traffic should use PostgreSQL, configure bridge-safe workers, keep their chosen Codex CLI tier setting, and turn on metrics/alerts before relying on it for daily traffic.

## Tier Semantics

Actual served tier is whatever upstream reports in the terminal response. If Codex CLI requests `ultrafast`, the baseline should verify whether `actual_service_tier` is also `ultrafast` or whether upstream returned another tier.

## Constraints

- Do not require PostgreSQL for zero-config startup.
- Do not recommend unconstrained multi-worker SQLite for sustained traffic.
- Do not change the operator's current Codex CLI ultrafast preference.
- Do not add a daily fast mode.
- Do not store secrets or bearer tokens in examples.

## Metrics Inventory

- Readiness and process health: `/health/ready`, `/backend-api/codex/health`, container state, and Helm kube-state alerts.
- Request status and latency: `codex_lb_requests_total`, `codex_lb_request_duration_seconds`.
- Upstream request volume and latency: `codex_lb_upstream_requests_total`, `codex_lb_upstream_request_duration_seconds`.
- Upstream failure classes and stream failures: `codex_lb_client_exposed_errors_total` and `codex_lb_failover_total`.
- SQLite lock pressure: `codex_lb_sqlite_lock_retries_total`.
- Bridge continuity: `codex_lb_continuity_fail_closed_total`, `codex_lb_continuity_owner_resolution_total`, and bridge owner/forward/rebind counters.
- Service-tier mismatch: `codex_lb_service_tier_mismatch_total`, plus request-log snapshot fields `requested_service_tier` and `actual_service_tier`.
