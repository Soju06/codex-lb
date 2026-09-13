## 1. Regression coverage

- [x] 1.1 Add deterministic order-ledger RED coverage for first-event,
  later-event, and raised `ProxyResponseError` owner-unavailable rewrites.
- [x] 1.2 Add deterministic RED coverage for empty-queue terminal settlement and
  unconfirmed settle/release health suppression.
- [x] 1.3 Exercise settlement ordering through streaming `POST /v1/responses`.

## 2. Implementation

- [x] 2.1 Reuse `_StreamSettlement` and
  `_settle_stream_usage_before_pending_penalty` at all three rewrite sites.
- [x] 2.2 Reuse
  `_finalize_terminal_settlement_after_downstream_close` for empty-queue
  terminal ordering.
- [x] 2.3 Preserve the owner-unavailable client/log envelope, original recovery
  code for health, and independent stale-anchor/source-ownership behavior.

## 3. Validation and delivery

- [x] 3.1 Run focused helper and `/v1/responses` route tests plus the deterministic
  order driver.
- [x] 3.2 Run Ruff, ty, LSP diagnostics, `make lint`, and strict OpenSpec
  validation.
- [x] 3.3 Review overlap with PRs #1955 and #1905, clean temporary artifacts,
  and prepare the exact verified head for publication.
