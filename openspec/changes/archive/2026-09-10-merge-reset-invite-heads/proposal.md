# Compose reset pooling with dashboard invitations

## Why

Main 561311ded adds invitations above audit attribution. The public upgrade-head CLI fails with two heads when composed with the published reset/user merge.

## What Changes

Append a no-op merge joining the two published parents. Preserve user-management route registration alongside Desktop routes. Verify populated upgrades and merge-only rollback without altering invitation, role, session or reset policy.

## Impact

Keep every published migration unchanged. Share the existing guarded disposable-database fixture with a separate invitation composition test so both run in hosted PostgreSQL. Pin historical merge-only assertions to their original merge before latest-head verification.
