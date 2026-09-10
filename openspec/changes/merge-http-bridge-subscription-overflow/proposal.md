# Merge HTTP bridge and subscription overflow migration heads

## Why

Upstream subscription-overflow schema and the deployed HTTP bridge recovery
schema descend independently from the quota-warmup migration. Rebasing must
preserve both histories while retaining a single upgrade target.

## What Changes

- Add a metadata-only Alembic merge revision joining both existing heads.
- Add a subsequent metadata-only merge for upstream's transport-default
  migration, retaining the previous merge and both independent histories.
- Verify upgrade from each branch and downgrade/upgrade of the merge preserve
  application data and the combined schema.

## Impact

- Database migration graph and migration regression tests only.
- No new settings or runtime routing behavior.
