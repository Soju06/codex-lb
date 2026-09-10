## Why

Main added guest-session revocation after the rejection/spool merge was published. Its migration branches from the spool parent, so the composed public `upgrade head` command has two heads again.

## What Changes

- Join the published rejection/spool merge and guest-session generation through a new no-op merge revision.
- Prove populated upgrades from each parent and both parent stamps, merge-only downgrade, roundtrip and schema drift.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: Preserve guest and rejection histories while restoring one upgrade head.

## Impact

One additional migration join, migration tests and current-main reconciliation. No guest-access, recovery, scope or settlement policy changes.
