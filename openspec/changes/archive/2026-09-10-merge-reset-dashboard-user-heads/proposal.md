# Compose reset pooling with dashboard users

## Why

Main 8e5760726 adds roles, users, credential reprojection and audit attribution above guest generation. Composing the published reset/guest merge produces two Alembic heads. Public upgrade-head fails before any upgrade can complete.

## What Changes

Append a no-op merge with the published reset/guest merge and audit-attribution head as parents. Preserve every published migration. Verify populated upgrades from each parent and merge-only downgrade/re-upgrade without changing authorization or reset policy.

## Impact

Existing audit rows, grants, identities, user and guest generations, credentials, reset bindings and retention values must survive. Main's credential backfill and reprojection still run when needed. Main's user-backed login and CSRF behavior remain authoritative.
