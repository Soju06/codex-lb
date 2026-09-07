## Why

Native WebSocket bursts can overflow the remaining 64-message queue even with a ready consumer, and local pressure currently penalizes healthy upstream accounts. The API-focused 8-CPU/16-GiB host has spare memory, but its two HA backends are capped at 1 GiB and their database pools are not budgeted across replicas.

## What Changes

- Replace message-count overflow with shared, byte-accounted per-socket and per-helper queue budgets, fair scheduling, owned cancellation, and account-neutral local-pressure failures.
- Extend opt-in HA Compose to three steady backends (blue, green, amber) plus temporary surge, with 3-GiB container limits and a 1-GiB WebSocket buffer budget per backend.
- Budget both database pools across all four possible processes; leave stock deployments' defaults unchanged.
- Preserve script-owned readiness/drain/recovery, support migration from existing HA markers, and adopt least-connection balancing through a verified graceful HAProxy reload.
- Update the deployment skill, SSOT and published deployment instructions; verify burst/concurrency and failure paths without generating paid upstream traffic.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: bounded byte buffering and account-neutral local WebSocket pressure with retained diagnostics.
- `deployment-installation`: three-backend HA capacity profile, surge rollout and operator guarantees.

## Impact

Native egress adapter, settings, WebSocket error classification, HA Compose/HAProxy/script, deployment skill, OpenSpec and tests. No database schema or API-key policy changes. Implementation does not itself authorize production deployment, commit or push. Full production throughput remains a measured result, not a promised concurrency number.
