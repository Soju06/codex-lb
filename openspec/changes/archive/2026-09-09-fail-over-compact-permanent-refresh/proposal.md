## Why

An account-neutral compact request can receive an upstream authentication
failure and then discover during forced refresh that the selected account's
OAuth credentials are permanently revoked. The proxy marks that account for
re-authentication but currently surfaces the original 401 instead of trying a
healthy account from the pool, so remote compaction blocks an otherwise
continuable Codex task.

## What Changes

- Treat a permanent post-401 refresh failure as an account-local compact
  failure when another account may safely receive the request.
- Mark and exclude the revoked account, then continue the existing bounded
  account-selection loop.
- Preserve fail-closed behavior for file/continuity-pinned compact requests and
  when no replacement account is available.
- Add product-path regressions for transparent pool failover and pin safety.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: extend compact authentication failover to permanent
  failures discovered by the forced refresh itself.

## Impact

Compact retry control flow and focused proxy tests only. No schema, setting,
dependency, dashboard, or deployment contract changes.
