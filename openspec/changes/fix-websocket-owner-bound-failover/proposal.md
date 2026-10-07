## Why

A retryable WebSocket handshake error can exclude the account that a request
still requires. Selection then replaces the original upstream failure with
`previous_response_owner_unavailable`. The HTTP failover path already passes
the required-owner constraint to the shared decision.

## What Changes

- Pass the existing hard account requirement to the WebSocket failover decision.
- Surface the original classified failure when an account change is forbidden.
- Preserve movable-request failover, account error classification, lease release,
  authentication refresh and transport-specific recovery.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: required-owner WebSocket connection failures preserve
  the original error instead of selecting an excluded required owner.

## Impact

One WebSocket decision call, focused existing unit coverage and this OpenSpec
change. No settings, dependencies, schema, account state or installation change.
