# Fix Codex label current-head evidence

## Why

The Codex label synchronizer can leave `needs work` on rebased pull requests
when a stale inline review thread remains unresolved but no longer belongs to
the current head. It can also misclassify duplicate check runs when an older
run completes after a newer rerun starts.

Both cases make PR readiness depend on stale GitHub evidence instead of the
current head.

## What Changes

- Filter unresolved Codex inline review threads with the same current-head and
  original-commit evidence rules used for REST review comments.
- Treat unresolved threads without current-head evidence as stale rather than
  blocking.
- Deduplicate check runs by run start/creation recency instead of completion
  time so late-finishing superseded runs cannot override newer reruns.

## Impact

- **Spec**: `github-automation`
- **Code**: `.github/scripts/sync_codex_ok_labels.py`
- **Tests**: `tests/unit/test_sync_codex_ok_labels.py`
