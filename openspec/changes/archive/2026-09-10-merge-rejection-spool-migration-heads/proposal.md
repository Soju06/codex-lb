## Why

PR #2330 composed with current main has two migration heads. The public `codex-lb-db upgrade head` command fails before migrating.

## What Changes

- Join rejection-generation and dashboard spool-retention history with a no-op merge revision.
- Verify populated upgrades from each parent, merge-only downgrade, and schema drift.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: Preserve both parent histories and provide one upgrade head.

## Impact

One Alembic merge revision and migration regression coverage. Current main is merged without rewriting either parent. No account recovery or proxy policy changes.
