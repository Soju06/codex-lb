## Why

Ambiguous HTTP Responses bridge disconnects can leave an upstream
`response.create` in flight. Recovery needs a durable, tenant-scoped operation
identity and transcript that survives process ownership changes without
replaying stale terminal events or deleting recoverable rows during startup.

## What Changes

- Keep durable operation fingerprints isolated by API-key scope.
- Preserve sessions with submitted, acknowledged, or unknown operations during
  startup takeover and detach their ownership for recovery.
- Reset an operation's event spool before rebinding an explicit failed retry.
- Only advance a continuation anchor when the completed sibling proves the same
  logical request fingerprint.
- Keep the operation-ledger migration lineage converged with the current main
  Alembic head.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: ambiguous HTTP bridge operations recover safely across
  owner changes and retries.

## Impact

The proxy durable repository, HTTP bridge request submission, startup takeover,
Alembic graph, and focused recovery tests are affected. Public request and
response shapes remain unchanged.
