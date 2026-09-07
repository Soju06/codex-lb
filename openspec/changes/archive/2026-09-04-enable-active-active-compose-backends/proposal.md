## Why

The single-host HA Compose topology currently keeps only one application slot eligible, so one process carries all traffic and a rollout temporarily depends on a single backend. Production CPU pressure and long-lived WebSocket traffic require two continuously active application backends plus a temporary surge backend so deployments preserve both admission and serving capacity.

## What Changes

- Run `server-blue` and `server-green` concurrently at equal positive HAProxy weights during steady state.
- Add a private `server-surge` backend used only while rolling both steady-state backends to a new image.
- Replace the single active/inactive cutover with a readiness-gated surge rollout that keeps at least two healthy backends eligible while each steady-state backend drains, updates, and rejoins.
- Preserve existing WebSocket and streaming connections on a draining backend until they complete or the configured drain bound expires.
- Add fail-closed recovery and explicit rollback behavior for a partially completed surge rollout.
- Update status output, production documentation, and the repository-owned deployment skill for the active-active topology.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: Change the opt-in single-host HA Compose topology and rollout contract from alternating blue/green slots to two steady-state active backends with one temporary surge backend.

## Impact

The HA Compose manifest, HAProxy configuration, host deployment script, deployment tests, Docker deployment documentation, deployment OpenSpec context, and `$codex-lb-ha-deploy` skill are affected. Stock Compose deployments, public APIs, database schema, and Helm deployments remain unchanged.
