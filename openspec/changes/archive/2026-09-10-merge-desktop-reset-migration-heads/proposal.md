## Why

Desktop reset pooling and current main have separate Alembic heads, so upgrading to head fails.

## What changes

Add an explicit no-op merge revision. Preserve both original revision histories and durable redemption bindings. Verify upgrades from either populated branch and downgrade of the merge itself.

## Impact

Database migration graph and migration regression tests only.
