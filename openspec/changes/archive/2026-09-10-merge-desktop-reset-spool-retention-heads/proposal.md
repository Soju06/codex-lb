# Join Desktop reset and spool-retention migrations

## Why

Main added dashboard spool retention after the prior reset repair passed hosted CI. Composing the branches creates two Alembic heads, reproduced by the public upgrade-head CLI.

## What Changes

Add an explicit no-op merge revision preserving both histories. Bind the earlier merge-only regression to its named revision, and test populated upgrades and merge-only downgrade for the new pair of parents.

## Impact

No product policy or existing migration operations change. Existing reset owner/credit bindings and dashboard retention values must survive composition.
