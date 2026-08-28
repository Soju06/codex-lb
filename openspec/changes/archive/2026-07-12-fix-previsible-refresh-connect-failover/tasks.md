## 1. Spec Delta

- [x] 1.1 Add a `responses-api-compat` requirement for file-pinned compact
  refresh/connect failures.
- [x] 1.2 Preserve the existing pre-visible failover contract for replayable
  compact/connect surfaces.
- [x] 1.3 Include issue trace for PR #822.
- [x] 1.4 Cover owner-bound bridge replay, stream lease release, and websocket
  response-create admission release edge cases raised in PR #1207 review.
- [x] 1.5 Require file-backed HTTP bridge precreated retries to reconnect on the
  pinned owner or fail closed.
- [x] 1.6 Keep stale same-model HTTP bridge previous-response aliases on the
  continuity-lost fail-closed path instead of model-transition rebind.

## 2. Verification

- [x] 2.1 Validate the OpenSpec change with `uv run openspec validate
  fix-previsible-refresh-connect-failover --strict`.
- [x] 2.2 Validate all specs with `uv run openspec validate --specs`.
