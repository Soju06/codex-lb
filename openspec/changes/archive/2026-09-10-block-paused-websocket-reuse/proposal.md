## Why

Ordinary direct Responses WebSocket follow-ups reuse their authenticated upstream socket without checking the shared account availability snapshot. An operator pause therefore stops fresh selection but can leave new turns flowing through an existing connection for minutes.

## What Changes

- Consult committed routing unavailability before reusing a direct WebSocket and again immediately before sending a response.create.
- Retire an idle unavailable socket through existing owner-switch cleanup and selection, preserving account-bound continuity.
- Reject only the new turn when an accepted sibling owns the socket; let accepted work finish and release rejected-turn reservations and admission.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Direct WebSocket reuse and final dispatch honor shared account unavailability.

## Impact

Direct WebSocket orchestration and route-level regression coverage. Uses the existing cross-replica availability cache and invalidation bus; no schema, setting, or automatic overload-policy changes.
