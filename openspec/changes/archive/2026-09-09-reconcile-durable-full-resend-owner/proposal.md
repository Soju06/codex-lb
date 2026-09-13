## Why

The fresh durable full-resend path can still fail before upstream dispatch when
a broad legacy session-header sticky row points at a different account than the
durable bridge owner. codex-lb returns `continuity_owner_conflict` as a
retryable 503, while the client repeats the same request and the two persisted
owners remain unchanged. This creates a deterministic retry loop even though
the request already contains fingerprint-verified complete context and can
safely start a fresh bridge on the durable owner.

## What Changes

- Represent complete durable full-resend eligibility with an immutable,
  request-bound internal proof created only by the count, fingerprint, and
  retained-output or response-bound pending-tool-call checks.
- For that proved fresh reattach only, stop consulting and forwarding the broad
  legacy session-header alias while retaining the durable canonical key and
  owner account as hard constraints.
- Preserve normal Codex session behavior on the replacement bridge so later
  incremental turns can use its newly established response anchor.
- Keep incomplete resends, conflicting specific aliases, and file-owner
  conflicts fail-closed; do not add or widen an account-movement path.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Reconcile a stale broad session mapping during a
  verified owner-bound fresh reattach without moving accounts.

## Impact

- Affected code: HTTP bridge durable full-resend verification and fresh session
  affinity preparation.
- Affected surface: hard Codex session reattach after the live upstream bridge
  is gone and a legacy raw session row disagrees with the durable owner.
- No new cross-account replay, schema, setting, dependency, or post-send retry.
