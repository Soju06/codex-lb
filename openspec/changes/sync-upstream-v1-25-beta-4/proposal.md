## Why

The fork is based on upstream `575c2087` and its three production backends still use AnyIO 4.13.0 with the HTTP bridge enabled. Upstream's cancelled-waiter regression reproduces a stranded Lock and Semaphore locally; the tagged beta.4 release includes the dependency fix, cancellation cleanup, routing, security, native HTTP fixes, and accepted-output-free retry improvements. The operator approved retargeting the in-progress beta.3 integration to beta.4.

## What Changes

- Integrate upstream `v1.25.0-beta.4`, peeled commit `15ccd901bf013daa11c67934cc019a9b33f2f72b`, into fork baseline `2930dec0`; retain upstream code, tests, lockfiles, and owning OpenSpec changes.
- Preserve all fork features: account quarantine, standalone key dashboard/installers, UTC+7 daily limits, multi-file import, proxy-pool assignment, native buffer/diagnostics, control-response fixes, and the three-backend HA surge workflow.
- Resolve the account eligibility, frontend route tree, and API-key context conflicts by combining their contracts; review auto-merged proxy paths as well.
- Validate the integrated candidate locally, record residual risks and exact results, and prepare an operator rollout checklist.
- Include the additional cancellation gate, accepted-output-free retry and plaintext-proxy warning commits (`7410ebe7`, `dafc1a21`, `a8339c8f`), with explicit regression checks for their fork interactions. Production rollout is a separate operator action after candidate verification.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-architecture`: make the fixed AnyIO synchronization behavior explicit in the fork's canonical contract; the upstream delta remains its detailed owner.
- `api-key-dashboard`: compose upstream route recovery with the standalone key-authenticated route without moving it under administrator authentication.
- `responses-api-compat`, `outbound-http-clients`, `upstream-proxy-routing`, and `frontend-architecture`: sync the beta.4 retry and plaintext-proxy warning deltas from their existing upstream owners so canonical specs do not retain superseded contracts.

Other imported behavior remains governed by the owning OpenSpec delta artifacts carried by the pinned upstream commit; this integration does not re-author their requirements or archive unrelated changes.

## Impact

Backend proxy/usage/security code, Python and Rust dependencies, frontend routing and dependencies, and their regression suites change. No new Alembic revision is introduced by the upstream range, but persisted bridge operation semantics gain `abandoned`, so mixed-version overlap needs explicit examination before HA rollout. Existing deployment scripts and settings remain fork-owned. No new environment variable, core navigation entry, or README section is introduced by the integration layer.
