# Proposal: proxy-native-history-notes-routes

## Why

Current Codex clients enable the native history-and-notes extension, which calls
`alpha/history/v2/*` and `alpha/notes/v2/*`. codex-lb does not expose those
routes, so the first `thread_hint` call reaches the proxy and returns `405`.

The native request has no session header. Its `context.session_id` is the
identity that must select the same upstream account as the corresponding Codex
Responses session. History and notes are account-local upstream state, so they
cannot be retried on another account.

## What Changes

- Expose the four native history routes and six native notes routes through the
  existing authenticated Codex-control proxy.
- Use a nonblank `context.session_id` from these route bodies solely as the
  control-request affinity identity. Forward the original request body and
  filtered inbound headers unchanged.
- Treat native history-and-notes calls as account-affine: preserve API-key
  account scope and do not fail over or retry them on a different account.

## Capabilities

### New Capabilities

- `native-history-notes-proxy`: authenticated passthrough for Codex native
  history and notes v2 operations.

### Modified Capabilities

- `upstream-proxy-routing`: specifies account affinity and no-cross-account
  retry for native history-and-notes operations.

## Impact

- `app/modules/proxy/api.py`: route registration and extraction of the native
  body session identity.
- `app/modules/proxy/_service/codex_control.py`: use that identity for existing
  account selection and suppress cross-account retry for these operations.
- `tests/integration/test_proxy_api_extended.py` and
  `tests/integration/test_daybreak_capability_routes.py`: wire and route-policy
  regression coverage.
