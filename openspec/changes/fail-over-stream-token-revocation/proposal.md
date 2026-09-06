## Why

Codex HTTP Responses streams can return a first-event `response.failed` with
`code=token_revoked` for one pooled account. The retry loop excludes that
account, but a response-owned reasoning item makes the dispatched body acquire
a temporary owner anchor. Selection then fails on that excluded owner with
`preferred_account_unavailable` instead of continuing on another healthy
account. The revoked account also remains routable, so later requests repeat
the same failure.

## What Changes

- Treat `token_revoked` as a permanent account-local authentication failure and
  mark the selected account `reauth_required`.
- Immediately remove access-token-revoked accounts from new routing while
  preserving the existing ability to use a still-valid access token when only
  its refresh token needs reauthentication.
- For a pre-visible, otherwise unanchored first turn, project known
  response-owned bookkeeping out of the dispatched body and fail over only
  when the resulting request passes the shared account-neutral replay gate.
- Preserve strict ownership for previous-response, file, turn-state, and
  single-account routes, and preserve the original authentication error when a
  body cannot move safely.
- Add a product-path regression matching Codex sticky full-history requests
  with encrypted reasoning state.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: recover HTTP Responses streams from a selected
  account's pre-visible revoked-token event without weakening hard ownership.

## Impact

Streaming retry, account eligibility/cache state, and focused proxy tests only.
No schema, setting, dependency, dashboard, or deployment contract changes.
