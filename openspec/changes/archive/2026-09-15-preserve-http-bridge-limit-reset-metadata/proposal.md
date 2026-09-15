## Why

The HTTP bridge's pre-created retry branches pass only the upstream error
message into account-health settlement. This drops `resets_at` and
`resets_in_seconds`, even though the parser retained them. A multi-day usage
limit becomes a short fallback cooldown, so request-path owner retirement
incorrectly treats the exhausted account as able to return within the request
budget. The same account can be selected again after that cooldown expires.

## What Changes

- Preserve parsed upstream reset metadata in the HTTP bridge retry health
  writes, including writes deferred until API-key reservation settlement.
- Use the existing typed upstream-error conversion and reset validation.
- Apply the same conversion to the equivalent direct WebSocket retry branches,
  which currently discard the same fields.
- Verify a hard-affinity HTTP request persists the real limit horizon and a
  subsequent request can retire that unavailable owner and use another account.
- Preserve file ownership, explicit continuation anchors, replay proof gates,
  ordinary metadata-free cooldowns, and reservation settlement ordering.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Preserve upstream reset evidence through bridge retry
  health settlement and existing owner recovery.

## Impact

- HTTP bridge and direct WebSocket upstream event handling and regression coverage.
- No schema, configuration, public API, or deployment migration changes.
