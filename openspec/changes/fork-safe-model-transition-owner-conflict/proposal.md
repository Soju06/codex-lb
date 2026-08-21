## Why

An HTTP bridge model transition can resolve a durable model owner while a
legacy hard alias resolves to a different account. The bridge then returns
`continuity_owner_conflict` before dispatch even when the request is a fresh,
account-neutral payload that can safely start a model-transition child lane.
Forwarded requests and payloads that still depend on account-scoped state must
remain fail-closed.

## What Changes

- Permit a model-transition child lane only for the exact
  `continuity_owner_conflict` error.
- Require a local request, no `previous_response_id`, no resolved file owner,
  and a payload proven account-neutral by the existing replay-safety validator.
- Clear session/turn aliases, exclude the conflicting owner, and create a
  server-namespaced account-neutral lane, pinned `hard`, without changing
  persisted aliases.
- Keep forwarded requests, unpinned hosted files, post-compaction payloads whose
  compacted context is not carried in the request, and other owner failures
  fail-closed.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: model-transition owner conflicts may use a
  proof-gated account-neutral child lane.
- `sticky-session-operations`: the hard-owner conflict exception is limited to
  that exact fresh model-transition case.

## Impact

- Affected code: HTTP bridge model-transition recovery and unit coverage.
- No schema, setting, dependency, or live deployment change.
- Rollback is a source revert; the existing fail-closed path remains the
  fallback for every request that does not satisfy the proof.
