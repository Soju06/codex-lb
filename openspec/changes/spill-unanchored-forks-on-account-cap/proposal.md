# Spill unanchored forks from capped preferred accounts

## Why

Self-contained unanchored parallel forks have no continuity owner, but a preferred-account cap currently sends them into the account-capacity wait even when another eligible account has headroom. This unnecessarily stalls fork work and contributes to the stream-cap starvation reported in #1354.

## What Changes

- When session creation rejects a self-contained `internal_unanchored_parallel` fork with a local account-cap error, drop its preferred-account hint once and retry selection.
- Keep owner-bearing requests pinned by requiring no previous response, conversation, input file reference, turn-state owner, or anchored forwarding context.
- Record the retry with the stable `unanchored_fork_cap_spill` bridge event.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: Allow self-contained unanchored forks to spill from a capped preferred account before waiting for capacity.

## Impact

- `app/modules/proxy/_service/http_bridge/streaming.py`: one-shot preferred-account removal and reselection.
- No setting, database migration, dashboard, or API schema change.
