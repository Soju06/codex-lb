## Why

Review found that a concurrent affinity write can be lost before probe rollback and that a relative migration can lose its target when it begins at a retained repair checkpoint.

## What Changes

- Require a provisional probe write to match the owner lookup that preceded it.
- Preserve request cancellation when probe rollback fails.
- Record the target of a relative upgrade that begins at a retained repair checkpoint.

## Capabilities

### Modified Capabilities

- `account-routing` covers sticky probe admission and cleanup.
- `database-migrations` covers interrupted historical usage-limit repairs.

## Impact

The changes affect provisional sticky writes and historical usage-limit ledger repair.
