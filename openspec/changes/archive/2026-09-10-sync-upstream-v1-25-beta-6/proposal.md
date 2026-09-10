## Why

The operator approved a complete v1.25.0-beta.6 integration rather than only selected backports. Integrating the frozen release must retain deployed key-dashboard data, UTC+7 limits, fork-native buffer behavior, and the verified fixes currently uncommitted on beta.5.

## What Changes

- Merge the complete beta.6 commit `e4c0164daae7e3ee3983d4455dee174441f76355`, including native transport interpretation, dashboard-managed settings, report history, and compatibility fixes.
- Resolve overlapping native transport, WebSocket metadata, API-key reset scheduler, settings reference, tests, and OpenSpec while preserving fork contracts.
- Add a new Alembic merge revision joining the deployed API-key group head and beta.6 report-rollup head, without rewriting the deployed group migration.
- Retain the previously verified HTTP hop-by-hop sanitation and burst settlement fixes that are not fully present in beta.6.
- Verify new dependency locks, native helper compatibility, frontend behavior, migration paths, and backend regressions before handoff.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: A beta.6 upgrade path preserves the fork's deployed key groups and converges to one migration head.
- `outbound-http-clients`: Native beta.6 response interpretation composes with the fork's WebSocket byte budgets and diagnostic metadata.

Other upstream capability changes arrive with the release's own OpenSpec artifacts and canonical specs; integration preserves and reconciles them instead of duplicating those requirements here.

## Impact

Full release integration across backend, native Rust helper, frontend, dependencies, migrations, documentation, and tests. No commit, push, production deployment, or runtime database mutation is included. Unrelated installer work remains untouched.
