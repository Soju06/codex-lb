## Why

Some deployed databases were stamped past the HTTP bridge recovery revisions
before all of their DDL reached the image.  A forward compatibility repair is
needed to restore the missing operation columns, indexes, and retained-alias
column without losing data when an operator downgrades.

## What Changes

- Add idempotent repair revision `20260911_070000_repair_http_bridge_recovery_columns`
  for the historical recovery schema objects.
- Attribute repaired objects to their historical migrations and preserve them
  on repair downgrade; re-home legacy repair markers in
  `20260912_020000_rehome_recovery_repair_ownership`.
- Keep the continuity-abandonment migration as a sibling of the thread-cache
  branch and converge both branches through the existing metadata-only merge.
- Add SQLite migration and downgrade coverage, including schema-drift parity.

## Impact

- Affected capability: `database-migrations`.
- No request-routing or persisted business-data changes are introduced.
