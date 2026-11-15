# Tasks

## 1. Regression coverage (RED)

- [x] 1.1 Extend `test_http_bridge_precreated_usage_limit_defers_keyed_health_until_settlement` to assert the queued penalty keeps the upstream reset deadline.
- [x] 1.2 Watch the new assertions fail against the message-only payload.
- [x] 1.3 Add `test_websocket_event_health_error_freezes_relative_reset_deadline` (helper matrix: relative-only freeze, valid absolute wins, expired absolute falls back to frozen relative, bool/string/zero/negative/beyond-horizon/NaN/inf rejected, `response.failed` envelope).
- [x] 1.4 Add `test_http_bridge_precreated_usage_limit_freezes_relative_reset_deadline` (bridge path: relative-only frame, deferred penalty, `_extract_reset_at` at observation+120s must still return the observed deadline).
- [x] 1.5 Refresh the original test's hardcoded `resets_at` (2026-05, now expired) to a dynamic future timestamp; the old value only passed because the message-only payload never validated it.

## 2. Implementation (GREEN)

- [x] 2.1 Add `_websocket_event_health_error` normalizer in `app/modules/proxy/_service/websocket/helpers.py` (message + reset metadata).
- [x] 2.2 Freeze relative `resets_in_seconds` into an absolute `resets_at` at the observation time (`_frozen_relative_reset_at`, mirroring `_extract_reset_at` bounds) and drop the relative field so it cannot re-anchor at settlement time.
- [x] 2.3 Use the normalizer in the three pre-created health branches of `app/modules/proxy/_service/http_bridge/upstream_events.py`, passing `now=time.time()`.
- [x] 2.4 Re-export through `service_stubs.py` and `app/modules/proxy/service.py`.

## 3. Verification

- [x] 3.1 `tests/unit/test_proxy_http_bridge.py` full file: 1066 passed.
- [x] 3.2 Related files (defer-cancellation, api-key-usage virtual time, proxy utils, load balancer): 1747 passed.
- [x] 3.3 `ruff check` on `app/` and the changed test file: all checks passed.
- [x] 3.4 Confirm settlement-ordering invariants unchanged (defer condition, drain timing, account ownership untouched).
- [ ] 3.5 `openspec validate` (openspec CLI not installed locally; delta written against the `advertise-gpt6-max-output-tokens` change format).
