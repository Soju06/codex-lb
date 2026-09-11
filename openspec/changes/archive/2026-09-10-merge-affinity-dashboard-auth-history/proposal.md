# Merge affinity and dashboard authentication migration histories

## Why

Main 8e5760726 adds roles, users and a compatibility-credential projection and audit actor fields after the guest migration. Combining it with the published affinity merge leaves two heads and prevents upgrade to head.

## What Changes

Append a no-op merge revision joining the published affinity merge with the current dashboard authentication leaf. Preserve every published migration body and parent. Verify populated upgrades from both histories, merge-only downgrade/reupgrade, permission and CSRF behavior, and SQLite/PostgreSQL execution.

## Impact

Migration graph and regression tests only. Existing role grants, authentication semantics, affinity metadata, routing and live rollout authority remain unchanged.
