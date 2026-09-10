# Join recovery and round-three dashboard settings

## Why

Rebasing the recovery branch onto current main introduces an independent
dashboard migration head. Both histories must remain upgradeable without
rewriting historical revisions.

## What Changes

- Add a metadata-only merge joining recovery/request budgets and conversation archive settings.
- Verify populated upgrades from each parent and direct parent downgrades.

## Impact

Only migration graph metadata changes; no schema or row mutations are introduced.
