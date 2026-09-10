## Why

A persisted upstream rate-limit hold can survive a restart even after the provider accepts requests again. The dashboard probe currently checks HTTP status without verifying stream completion, so it cannot safely authorize recovery.

## What Changes

- Persist a monotonic block generation and the rejected request's model and service tier.
- Require completed provider execution matching the recorded scope before the existing dashboard probe may clear a hold.
- Compare the pre-probe generation and identity at settlement; preserve newer rejection, pause, deletion and reauthentication state.
- Keep historical unknown-scope holds under existing recovery rules.

## Capabilities

### New Capabilities

### Modified Capabilities

- `account-routing`: explicit completed-probe recovery with durable rejection identity.
- `usage-refresh-policy`: completed execution before advisory success and additive probe outcome fields.

## Impact

Account schema and Alembic migration, account repository, proxy rejection settlement, existing dashboard account probe, and route/migration regression tests. No new endpoint, automatic probing, settings, or owner-affinity changes.
