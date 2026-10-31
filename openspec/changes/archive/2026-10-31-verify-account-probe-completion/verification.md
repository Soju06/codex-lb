## Verification

- Final sender, service and actual `/probe` API suite: 84 passed.
- Normal transport regression suite: 92 passed (`test_proxy_upstream_fingerprint`,
  `test_native_stream_fixtures`, `test_http_subscription_routing_hint`,
  `test_cache_isolation_probe_sender`).
- Coverage includes HTTP-200 auth/quota/model/safety distinctions, Bearer/token/
  JWT/API-key redaction, message bounds, structured HTTP errors, oversized and
  unstructured bodies, timeouts, fragmented SSE, cancellation, account-pinned
  proxy routing, preserved snapshots and legacy wakeup signature compatibility.
  Tests exercise the actual core stream path through lower transport doubles,
  including native egress, normal fingerprint and installation identity, and
  omission of unsupported wire fields.
- Ruff check and format check on all owned Python files: passed.
- `ty check` on the accounts implementation and core stream client: passed.
- Cancellation safety check: passed.
- Final proxy architecture check: passed.
- Strict validation of `verify-account-probe-completion`: passed.
- Repository-wide `openspec validate --specs`: 66 passed, zero failed, with existing warnings.
  Repository-wide strict validation fails on pre-existing spec diagnostics
  (8 passed, 58 failed), primarily requirement-length warnings. Main specs were
  not edited by this change.

All network responses in verification were local test doubles. No live account
requests, secrets, commits or deployment were needed. The active OpenSpec change
is retained for integration with the parent's frontend work.
