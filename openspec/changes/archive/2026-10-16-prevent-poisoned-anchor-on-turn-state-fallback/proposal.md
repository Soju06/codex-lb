## Why

A generated turn-state alias can resolve to an older durable row while local
session creation falls back to the request's thread key. Planning can then
inject an anchor already poisoned on the thread key, repeatedly rejecting a
client that already sent its full history.

## What Changes

- Include the supported turn-state fallback key in pre-planning circuit checks.
- Suppress proxy anchor injection/trim when either routing key fences it.
- Preserve explicit client anchors, delta-only refusal, account ownership,
  API-key scope, and sibling-thread isolation.
- Add real native HTTP route regressions for split durable rows.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: full resends honor poisoned fallback-key anchors
  before planning, through durable and session-level injection.

## Impact

HTTP bridge planning and native HTTP regression coverage. No schema, setting,
dependency, client configuration, or production data migration. Independent of
the backoff-selector fix in #2627 and quota metadata fix in #2626.
