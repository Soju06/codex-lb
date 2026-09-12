- [x] Add a distinct `bridge_eventless_timeout` classification for every
  pre-response-start HTTP bridge terminal (logs, request-log detail/phase,
  retry-circuit `last_detail`, client error, Prometheus surface label).
- [x] Keep the client-visible shape retryable (`503`) with a message that does
  not blame the upstream.
- [x] Replace the implicit `_STREAM_KEEPALIVE_MAX_COUNT * sse_keepalive_interval_seconds`
  pre-response deadline with the named settings-derived
  `_http_bridge_eventless_budget_seconds`, aligned with the owner-side stuck gate.
- [x] Record an `unmatched_upstream_liveness` marker and per-session counter for
  upstream frames that prove liveness but match no pending request; exclude
  locally injected `codex.keepalive`.
- [x] Make the four local bridge reset sites say local bridge reset and reserve
  `Upstream websocket closed ...` for real upstream closes; keep those resets
  account-health neutral.
- [x] Add regressions for the new classification, the surviving post-start
  `stream_idle_timeout`, the grep-style local-reset assertion, the
  unmatched-liveness marker, and the budget invariants.
- [x] Run the branch regression files, `tests/unit/test_proxy_http_bridge.py`,
  and `tests/integration/test_migrations.py`.
- [x] Record the timeout-invariant linter rules as a follow-up note, since
  `app/core/timeout_invariants.py` is not on this branch lineage.
