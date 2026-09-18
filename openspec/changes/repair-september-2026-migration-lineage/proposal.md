## Why

`20260914_000000_add_scim_tokens` and the original overflow-retirement
revision were merged independently on the same parent and used the same
timestamp slot. Alembic therefore sees two heads and refuses every normal
startup migration with `MultipleHeads`.

## What Changes

- Restamp the later, unreleased overflow-retirement revision as
  `20260918_171648_drop_subscription_overflow_schema`.
- Chain it after `20260914_000000_add_scim_tokens` so the graph stays linear.
- Prove a disposable SQLite database can upgrade, downgrade to the shared
  parent, and upgrade again with the expected schema from both branches.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: restore one ordered migration head without a duplicate
  timestamp slot.

## Impact

- The overflow migration keeps its existing guarded schema operations. Only its
  revision identity and parent change.
- Startup migration can target `head` again.
