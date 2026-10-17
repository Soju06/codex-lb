## Why

When two eventless upstream attempts open the HTTP bridge retry circuit, Codex
Desktop immediately retries the same hard turn-state request. The bridge
currently returns a startup 503 before consulting the durable operation ledger.
Codex does not honor the full retry-circuit delay and can exhaust its client
retry budget during the cooldown, pausing the task even though the bridge and
VPS remain healthy.

## What Changes

- In an explicitly enabled server recovery mode, hold a turn-state-only hard
  continuation through the active retry-circuit cooldown before submission.
- Require a live durable session id and owner epoch, zero response events, and
  no response id before waiting.
- Dispatch nothing while waiting. After cooldown, use the existing durable
  operation ledger and one-shot/indefinite recovery policy to arbitrate whether
  the request may be created, claimed, replayed, or failed closed.
- Preserve the current immediate 503 for the default `fail_closed` mode,
  in-memory fallback sessions, soft affinity, and eventful requests.
- Emit a low-cardinality bridge event when the operation-fenced wait begins.

## Impact

- HTTP Responses bridge startup behavior during retry-circuit cooldown.
- No database schema, public API, account routing, or default configuration
  change.
