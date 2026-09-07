## Why

The production Compose deployment publishes the application container directly, so replacing that container necessarily interrupts new connections. Operators need an opt-in front proxy and blue/green rollout command that can start and validate a replacement before removing the active backend from service.

## What Changes

- Add an opt-in HAProxy Compose overlay that owns the existing public HTTP port and routes to two independently named codex-lb backend slots.
- Add a deployment script that builds and starts the inactive slot, verifies readiness, switches HAProxy admission, drains the old slot, and then stops it.
- Preserve long-lived HTTP, SSE, and WebSocket connections during normal cutover while sending new connections to the replacement.
- Fail closed before cutover when the deployment does not meet multi-replica prerequisites, when the candidate is unhealthy, or when HAProxy cannot accept the runtime change.
- Document initial installation, repeated deployment, rollback behavior, topology/security prerequisites, and the bounded-drain limitation.
- Add a repository-owned Codex skill that automatically selects the HA rollout workflow for later single-host Compose deployment requests.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: Add a supported opt-in Docker Compose HAProxy blue/green deployment topology and operator workflow.
- `replica-operations`: Define the shared-state and instance-identity prerequisites during blue/green overlap.

## Impact

- New HAProxy configuration and HA Compose overlay under `deploy/compose/`.
- New operator deployment script under `scripts/`.
- New project deployment skill under `.agents/skills/` and a matching repository workflow rule.
- Docker deployment documentation and deployment/replica OpenSpec context are extended.
- Existing default `docker-compose.yml` and `docker-compose.prod.yml` remain single-replica and unchanged in behavior.
- The HA path requires Docker Compose, shared PostgreSQL, shared encryption-key storage, and a continuously running HAProxy container.
