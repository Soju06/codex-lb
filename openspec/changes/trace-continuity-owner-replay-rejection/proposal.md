## Why

`previous_response_owner_unavailable` is the user-visible face of #1707: an existing Codex
thread whose upstream owner account became unroutable fails with 502 on every resume while
healthy accounts sit idle in the pool. The proxy already contains the cure —
`switch_to_account_neutral_replay` in `app/modules/proxy/_service/http_bridge/streaming.py`
projects reasoning out of the history and re-sends plaintext context on a fresh account — and
its gate, `durable_full_resend_allows_account_neutral_replay`, is a conjunction of six
independent proofs. It returns a bare `bool`.

That makes the failure unattributable. A 36-hour window on a production deployment
(`1.25.0-beta.7`, 24 accounts) recorded **1,480** `previous_response_owner_unavailable`
request-log rows, and **zero** `owner_unavailable_fresh_resend` or `dead_owner_fresh_resend`
bridge events in the same period: the recovery path never fired once, and nothing on the
failing request says which of the six proofs refused it. The maintainer's 2026-09-08 status
note on #1707 asks reporters for exactly this attribution and the current logs cannot supply
it.

The same window also shows the failure is invisible where operators look for it. All 1,491
`previous_response_owner_unavailable` rows over 48 hours carry `transport="websocket"` — every
one came from the direct WebSocket surface, which logs the code itself. Not one carries
`transport="http"`: the HTTP session bridge raises the identical envelope from session
creation, before any request-log write, and `app/modules/proxy/_service/http_bridge/` contains
no request-log write at all (`_write_request_log` appears only as an unused protocol
declaration). Over the most recent 6 hours the container log carried the bridge's 502s while
`request_logs` held **0** of them out of 14,555 rows. The dashboard shows nothing, so a bridge
incident is only reachable by reading a rotating container log (3x50MB, roughly 8 hours of
retention) — which is how the 2026-09-04 recurrence of this same class had to be
reconstructed.

## What Changes

- **Typed rejection reason.** `durable_full_resend_allows_account_neutral_replay` becomes a
  thin `is None` wrapper over a new `classify_account_neutral_replay_rejection()` that returns
  `None` when a cross-account replay is allowed and otherwise one of a closed set:
  `file_bound`, `no_durable_lookup`, `payload_not_full_resend`, `anchor_metadata_missing`,
  `prefix_fingerprint_mismatch`, `input_not_itemized`, `missing_prior_output`,
  `account_scoped_input`. The stored-anchor half moves to a new module-level
  `_durable_full_resend_anchor_rejection(payload, lookup, *, payload_looks_like_full_resend)`
  — pure, and the seam the unit tests drive — and `classify_durable_full_resend` gains a fourth
  tuple element carrying its verdict, so the three causes it collapses today
  (`payload_not_full_resend`, `anchor_metadata_missing`, `prefix_fingerprint_mismatch`) stop
  being indistinguishable. `input_not_itemized` is the fourth branch and stays a guard: a
  non-list body cannot satisfy the stored-prefix proof, so the prefix check always refuses
  first. The predicate's truth value is unchanged at every existing call site.
- **One emission point.** `owner_unavailable_allows_account_neutral_replay` emits the new
  bridge event `owner_unavailable_replay_rejected` (WARNING, `detail=reason=<reason>`) when the
  raised error is an owner-unavailable failure and the classifier refuses. It is the single
  funnel for all three of its call sites (session creation, owner forward, capacity retry), so
  no raise site is instrumented individually. The `required_continuity_owner_missing`
  fail-closed raise has no exception to classify, so it calls the classifier directly and
  emits the same event with whichever reason applies.
- **Metric.** New counter `codex_lb_continuity_replay_rejected_total{surface, reason}`
  registered in `app/core/metrics/prometheus.py` with the usual `None` fallback, recorded
  through a new `_record_continuity_replay_rejected` in
  `app/modules/proxy/_service/observability.py` next to the existing
  `_record_continuity_owner_resolution` and `_record_continuity_fail_closed`.
- **Request-log parity for the bridge surface.** `_stream_http_bridge_or_retry` writes one
  `_write_stream_preflight_error` row before re-raising a pre-submit `ProxyResponseError` whose
  code is a continuity-owner code (`previous_response_owner_unavailable`,
  `continuity_owner_conflict`). This mirrors what `retry.py` already does for the `http_stream`
  surface at its own owner-unavailable exit, and uses the request id the container log prints
  (both sites resolve the same `ensure_request_id()` contextvar), so log and row are joinable.
  The write sits on the propagating branch only: when the bridge instead hands the turn to the
  raw-HTTP upstream, that path settles its own outcome, and an error row for a turn that then
  succeeded would corrupt the error rate this row exists to expose. Only those codes are
  logged; widening the bridge's request-log coverage to every pre-submit failure is a separate
  concern.

No new `CODEX_LB_*` setting, no migration, no `.env.example` change, no README growth. No
routing, selection, or failover decision changes: the classifier is a pure refactor of an
existing conjunction, and the counter and log line are observational. None of the four
ceiling-guarded proxy files (`service.py`, `load_balancer.py`,
`_service/http_bridge/mixin.py`, `_service/streaming/mixin.py`) is touched.

## Why observability first

The follow-up work in #1707 is a behavior change: retiring an unroutable continuity owner so
the thread rebinds. Which mechanism to build depends on which proof is actually refusing the
existing recovery in production, and `no_durable_lookup` versus `prefix_fingerprint_mismatch`
point at different fixes. Landing the attribution first makes that choice evidence-based and
gives the later PRs a before/after signal that is not a rotating container log.

## Impact

- Affected specs: `proxy-runtime-observability` (ADDED — rejection attribution requirement,
  alongside the existing continuity fail-closed contracts).
- Affected code: `app/modules/proxy/_service/http_bridge/streaming.py`,
  `app/modules/proxy/_service/http_bridge/helpers.py` (one event name added to the WARNING
  set), `app/modules/proxy/_service/observability.py`, `app/core/metrics/prometheus.py`.
- Tests: `tests/unit/test_http_bridge_replay_rejection.py` (new; the anchor-proof reasons,
  the counter's closed label set, the event's WARNING level), `tests/unit/test_metrics.py`,
  `tests/integration/test_http_responses_bridge.py` (an owner-unavailable 502 through the real
  bridge emits the event and leaves exactly one `request_logs` row carrying the ingress request
  id).
- Partial fix for #1707; it does not by itself let any thread recover.
