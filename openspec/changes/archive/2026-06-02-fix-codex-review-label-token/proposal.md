## Why

The `Codex review labels` workflow is failing repeatedly even though the label classifier itself can read PR state. The failing runs all stop on write operations such as adding/removing `🤖 codex:*` labels or posting `@codex review`, with GitHub returning `Resource not accessible by integration (HTTP 403)` from the workflow token.

## What Changes

- Prefer a repository-provided write token (`CODEX_LABEL_SYNC_TOKEN`, then existing `RELEASE_PLEASE_TOKEN`) before falling back to `github.token`.
- Keep the workflow on the trusted default-branch checkout so privileged tokens are not exposed to PR code.
- Add an explicit tolerant-write mode: read/classification errors still fail, but GitHub App write-denial responses are logged as per-PR warnings and the workflow continues processing the remaining PRs.
- Add unit coverage for tolerated label/comment write denials and workflow token selection.

## Impact

- Repeated red `Codex review labels` runs caused by token write-denial should stop.
- When a privileged token is available, labels/comments are written normally.
- If the workflow falls back to a read-only token, it reports the skipped write in logs instead of making every CI completion red.
