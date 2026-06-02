## 1. Workflow token

- [x] 1.1 Prefer `CODEX_LABEL_SYNC_TOKEN` for Codex label synchronization writes.
- [x] 1.2 Fall back to the existing `RELEASE_PLEASE_TOKEN`, then `github.token`.
- [x] 1.3 Keep checkout pinned to the trusted default branch with persisted credentials disabled.

## 2. Write-denial handling

- [x] 2.1 Add a script flag to tolerate GitHub App `Resource not accessible by integration (HTTP 403)` write denials.
- [x] 2.2 Apply the tolerant path only to label/comment/workflow-approval writes; read/classification failures still fail the run.
- [x] 2.3 Log per-PR write warnings so skipped mutations remain visible.

## 3. Verification

- [x] 3.1 Add unit tests for tolerated label write denials.
- [x] 3.2 Add unit tests for tolerated Codex review comment denials.
- [x] 3.3 Add unit coverage for workflow token precedence and tolerant apply invocation.
- [x] 3.4 Run focused tests, lint, and OpenSpec validation.
