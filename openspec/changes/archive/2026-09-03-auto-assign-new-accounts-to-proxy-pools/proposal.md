## Why

New accounts created through `auth.json` import or OAuth currently remain
unbound until an operator manually selects a proxy pool. That leaves an
avoidable direct-egress or import-paused window and makes balanced proxy
assignment an ongoing manual operation.

## What Changes

- Automatically create an active account-to-pool binding when a new local
  account row is created and at least one active, structurally usable proxy
  pool exists.
- Select a pool with the fewest active account bindings, with a stable
  tie-break, so sequential account additions remain balanced.
- Persist the account and its initial proxy binding atomically before any
  import-time usage refresh can open a network connection.
- Preserve existing bindings during re-import and reauthentication, and retain
  existing unbound behavior when no usable pool exists.
- Invalidate upstream-route caches after an automatic binding is committed.
- Add no setting or migration; the behavior follows the existing proxy-pool
  configuration and remains zero-configuration.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `upstream-proxy-routing`: define automatic, balanced initial pool binding for
  accounts newly created by import or OAuth.

## Impact

- Account creation persistence in `app/modules/accounts/repository.py` and a
  focused upstream-proxy assignment helper.
- `auth.json` imports and untargeted OAuth account creation; targeted
  reauthentication and in-place imports retain their existing binding.
- Upstream-route cache invalidation and focused repository/integration tests.
- No API schema, database schema, environment variable, or dependency change.
