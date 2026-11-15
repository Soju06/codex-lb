# Tasks

## 1. Regression coverage (RED)

- [x] 1.1 Extend `test_http_bridge_precreated_usage_limit_defers_keyed_health_until_settlement` to assert the queued penalty keeps the upstream reset deadline.
- [x] 1.2 Watch the new assertions fail against the message-only payload.
- [x] 1.3 Add `test_websocket_event_health_error_freezes_relative_reset_deadline` (helper matrix: relative-only freeze, valid absolute wins, expired absolute falls back to frozen relative, bool/string/zero/negative/beyond-horizon/NaN/inf rejected, `response.failed` envelope, horizon clamp at fractional observation, raw beyond-horizon absolute does not preempt a valid relative, elapsed frozen deadline does not resurrect).
- [x] 1.4 Add `test_http_bridge_precreated_usage_limit_freezes_relative_reset_deadline` (bridge path with a pinned caller clock: known observation instant, frozen absolute deadline, no relative field, `_extract_reset_at` 120s later still returns the observed deadline).
- [x] 1.5 Refresh the original test's hardcoded `resets_at` (2026-05, now expired) to a dynamic future timestamp; the old value only passed because the message-only payload never validated it.

## 2. Implementation (GREEN)

- [x] 2.1 Add `_websocket_event_health_error` normalizer in `app/modules/proxy/_service/websocket/helpers.py` (message + reset metadata).
- [x] 2.2 Freeze relative `resets_in_seconds` into an absolute `resets_at` at the observation time and drop the relative field so it cannot re-anchor at settlement time.
- [x] 2.3 Share the metadata rules through `freeze_reset_deadline` in `app/core/balancer/logic.py`: strict raw-absolute check (no rounding slack, mirroring the consumer), relative frozen to a whole-second deadline clamped into the horizon measured from the observation time (ceil can land fractionally past it and the consumer re-checks without slack).
- [x] 2.4 Use the normalizer in the three pre-created health branches of `app/modules/proxy/_service/http_bridge/upstream_events.py`, passing `now=clock.time()` from the injected clock (raw `time.time()` is rejected by `scripts/check_proxy_timing_seams.py`).
- [x] 2.5 Re-export through `service_stubs.py` and `app/modules/proxy/service.py`.

## 3. Verification

- [x] 3.1 `tests/unit/test_proxy_http_bridge.py` full file: passed.
- [x] 3.2 Related files plus `test_load_balancer.py`: passed.
- [x] 3.3 `ruff check` on `app/` and the changed test file: all checks passed.
- [x] 3.4 `scripts/check_proxy_timing_seams.py`: exit 0 (no raw-clock-read sites).
- [x] 3.5 Confirm settlement-ordering invariants unchanged (defer condition, drain timing, account ownership untouched).
- [ ] 3.6 `openspec validate` (openspec CLI not installed locally; delta written against the `advertise-gpt6-max-output-tokens` change format).
