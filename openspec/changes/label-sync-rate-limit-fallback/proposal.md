## Why

The Codex label sync workflow authenticates with a user PAT (`CODEX_LABEL_SYNC_TOKEN`), whose 5,000/hr REST quota is shared with every other consumer of that user's token (interactive sessions, agents, other automations). During busy review cycles the quota exhausts and every label sync run fails with `API rate limit exceeded (HTTP 403)`, painting spurious CI failures on open PRs for up to an hour — observed repeatedly on 2026-07-13 during the adaptive-windows review cycle (#1266/#1267/#1268).

## What Changes

- The sync script detects rate-limit exhaustion on any `gh` call and switches once to a fallback token (`GH_FALLBACK_TOKEN`), retrying the failed call; the workflow provides `github.token` as that fallback, which carries a separate per-repository Actions quota.
- When no distinct fallback is available (or it is also exhausted), behavior is unchanged: the run fails per the read/classification failure contract.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `github-automation`: label sync gains a runtime rate-limit token fallback on top of the existing configuration-time token preference.

## Impact

- Code: `.github/scripts/sync_codex_ok_labels.py`, `.github/workflows/codex-review-labels.yml`
- Tests: `tests/unit/test_sync_codex_ok_labels.py`
- Specs: `openspec/specs/github-automation/spec.md`
