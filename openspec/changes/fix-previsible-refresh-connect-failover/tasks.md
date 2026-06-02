## 1. Spec Delta

- [x] 1.1 Add a `responses-api-compat` requirement for file-pinned compact
  refresh/connect failures.
- [x] 1.2 Preserve the existing pre-visible failover contract for replayable
  compact/connect surfaces.
- [x] 1.3 Include issue trace for PR #822.

## 2. Verification

- [x] 2.1 Validate the OpenSpec change with `uv run openspec validate
  fix-previsible-refresh-connect-failover --strict`.
- [x] 2.2 Validate all specs with `uv run openspec validate --specs`.
