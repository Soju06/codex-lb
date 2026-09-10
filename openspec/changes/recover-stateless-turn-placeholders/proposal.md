## Why

PR #1905 treats an unregistered proxy-shaped turn-state header as a continuation even when the request has no previous response and needs no stored account state. With multiple accounts, a first request or transport fallback returns `previous_response_owner_unavailable` before upstream dispatch.

## What Changes

- Keep an unregistered synthetic marker as a placeholder when the complete original request passes the existing account-neutral fresh-replay validator.
- Apply the same rule to HTTP bridge, raw HTTP and direct WebSocket admission.
- Preserve registered marker owners, previous-response ownership, file pins, opaque state and ambiguous incomplete continuations.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: distinguish stateless placeholder requests from owner-miss continuations.

## Impact

Changes the three #1905 continuation-admission conditions and public route regressions. No new setting, schema, dependency, frontend or migration. This corrects the existing PR, which remains linked to #2274.
