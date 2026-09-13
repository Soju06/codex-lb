## Why

The repository's `needs rebase` label is maintained manually even though the
existing Codex label workflow already resynchronizes every open pull request
every 15 minutes. A resolved conflict can therefore leave a stale blocker, and
base-branch lag or review policy can be mistaken for a conflict.

## What Changes

- The Codex label synchronizer adds `needs rebase` only for confirmed conflict
  states.
- It removes the label for every known mergeable or non-conflict state,
  including `BEHIND`, `BLOCKED`, `HAS_HOOKS`, and `UNSTABLE`.
- It preserves the current label only while GitHub reports an unknown merge
  state.
- Focused tests cover the state mapping and exact add/remove API writes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `github-automation`: the scheduled label sync also owns `needs rebase`
  freshness.

## Impact

- Code: `.github/scripts/sync_codex_ok_labels.py`
- Tests: `tests/unit/test_sync_codex_ok_labels.py`
- Specs: `openspec/specs/github-automation/spec.md`
