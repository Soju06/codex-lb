## 1. Implementation

- [x] 1.1 Add the closed reason set to `app/modules/proxy/_service/http_bridge/streaming.py` as
  a module-level `Literal` alias plus a `frozenset` used by the tests, and give
  `classify_durable_full_resend` a fourth tuple element carrying the reason it cleared the
  anchor fields (`payload_not_full_resend`, `anchor_metadata_missing`,
  `prefix_fingerprint_mismatch`, `input_not_itemized`). Store it in a new
  `durable_full_resend_anchor_reason` request-local.
- [x] 1.2 Introduce `classify_account_neutral_replay_rejection()` in
  `_stream_via_http_bridge_impl`, evaluated in the same order as today's conjunction:
  `file_bound` -> anchor-reason (or `no_durable_lookup` when no durable lookup was supplied) ->
  `missing_prior_output` -> `account_scoped_input`. Reduce
  `durable_full_resend_allows_account_neutral_replay()` to
  `classify_account_neutral_replay_rejection() is None`; the two `assert`s it carries today are
  subsumed by the explicit early checks and are removed. Leave its three other call sites
  (`streaming.py` dead-owner fresh resend and the two stale-anchor gates) untouched.
- [x] 1.3 Emit `owner_unavailable_replay_rejected` from
  `owner_unavailable_allows_account_neutral_replay` when the error is an owner-unavailable
  failure and the classifier refuses, and from the `required_continuity_owner_missing` raise
  (which has no exception to classify). One emission per refusal; assert no double emission
  when the connect loop re-enters.
- [x] 1.4 Add `owner_unavailable_replay_rejected` to the WARNING event set in
  `app/modules/proxy/_service/http_bridge/helpers.py::_log_http_bridge_event`.
- [x] 1.5 Register `continuity_replay_rejected_total`
  (`codex_lb_continuity_replay_rejected_total{surface,reason}`) in
  `app/core/metrics/prometheus.py` with the `None` fallback and the `__all__` entry, and add
  `_record_continuity_replay_rejected` to `app/modules/proxy/_service/observability.py`
  following `_record_continuity_owner_resolution`'s `_service_global` indirection.
- [x] 1.6 In `_stream_http_bridge_or_retry`, capture a monotonic start alongside `request_id`
  and, in the `except ProxyResponseError` branch before the `raise`, write one
  `_write_stream_preflight_error` row when the sanitized error code is
  `previous_response_owner_unavailable` or `continuity_owner_conflict`. Derive
  `useragent`/`useragent_group`/`conversation_id` with
  `_request_log_client_fields` from `app/modules/proxy/_service/support.py` (an allowed import
  domain for `_service/http_bridge/**`). Write inside the propagating branch, not before it: the
  transport-failover branch hands the turn to raw HTTP, which settles its own outcome.

## 2. Regression coverage

- [x] 2.1 New `tests/unit/test_http_bridge_replay_rejection.py`: the anchor-proof reasons via
  the module-level `_durable_full_resend_anchor_rejection`, a case proving the allowed path
  reports no reason, a case proving `payload_not_full_resend`, `anchor_metadata_missing` and
  `prefix_fingerprint_mismatch` are reported distinctly where today all three collapse to one
  `False`, and a case pinning that `input_not_itemized` stays unreachable behind the prefix
  proof. The event's WARNING level is asserted through `caplog`, not bytecode introspection.
- [x] 2.2 `tests/integration/test_http_responses_bridge.py`: an end-to-end bridged resume whose
  owner account is not selectable returns the unchanged 502 envelope, logs exactly one
  `owner_unavailable_replay_rejected` with a reason from the closed set, and leaves exactly one
  `request_logs` row for the ingress request id with
  `error_code="previous_response_owner_unavailable"`. A sibling case proves a capacity-coded
  pre-submit failure still writes no row.
- [x] 2.3 Assert the counter's label set is closed and that the recorder no-ops without
  `prometheus_client`, matching the existing continuity-counter tests; add the name/label
  assertion to `tests/unit/test_metrics.py` next to the sibling continuity counters.
- [x] 2.4 Keep `tests/unit/test_proxy_http_bridge.py` (which pins the 502/503 owner-unavailable
  envelopes) and `tests/unit/test_otel.py` green.

## 3. Validation

- [x] 3.1 `make lint` (includes `scripts/check_proxy_architecture.py`: no ceiling-guarded file
  grew, no cross-domain import added), `make typecheck`.
- [x] 3.2 `make test-unit` (9,984 passed), `make test-integration-bridge` (341),
  `make test-integration-core-1` (889), `-2` (873), `-3` (774), `tests/e2e` (27). Shards 2 and 3
  also reported sqlite `unable to open database file` on unrelated files (444 occurrences)
  from a 96%-full host partition shared with concurrent suites; those files pass in
  isolation (103 + 247).
- [x] 3.3 `openspec validate trace-continuity-owner-replay-rejection --strict` and
  `openspec validate --specs`.
- [x] 3.4 `codex review --base origin/main` — no findings.
