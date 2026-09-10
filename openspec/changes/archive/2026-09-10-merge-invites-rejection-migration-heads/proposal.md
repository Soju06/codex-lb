## Why

Main `561311ded` appends the invite-table migration after audit attribution while the published dashboard-user/rejection merge `200000` remains a separate head. Public upgrade head must converge again without rewriting published history.

## What Changes

Append a no-op merge `220000` joining `200000` and `040000` invite history. Preserve populated invites, roles, users, credentials, generations, ownership, audit and rejection data through upgrades and merge-only roundtrips.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: converge invite and rejection histories.

## Impact

One new merge revision, focused migration tests and PostgreSQL selection, verified spec sync/archive. Existing invite/permission/probe behavior stays intact.
