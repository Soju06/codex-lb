# Tasks

## 1. Regression coverage (RED)

- [x] 1.1 Extend `test_http_bridge_precreated_usage_limit_defers_keyed_health_until_settlement` to assert the queued penalty keeps `resets_at` / `resets_in_seconds`.
- [x] 1.2 Watch the new assertions fail against the message-only payload.

## 2. Implementation (GREEN)

- [x] 2.1 Add `_websocket_event_health_error` normalizer in `app/modules/proxy/_service/websocket/helpers.py`.
- [x] 2.2 Use it in the three pre-created health branches of `app/modules/proxy/_service/http_bridge/upstream_events.py`.
- [x] 2.3 Re-export through `service_stubs.py` and `app/modules/proxy/service.py`.

## 3. Verification

- [x] 3.1 `tests/unit/test_proxy_http_bridge.py` full file: 1064 passed.
- [x] 3.2 Related files (`test_defer_cancellation_shield_leak.py`, `test_proxy_api_key_usage_virtual_time.py`, `test_proxy_utils.py`): 1433 passed, 1 pre-existing failure (`test_chat_startup_probe_consumes_repeated_capacity_markers_before_first_event`, fails on clean `origin/main` too).
- [x] 3.3 `ruff check` on changed files: all checks passed.
- [x] 3.4 Confirm settlement-ordering invariants unchanged (defer condition, drain timing, account ownership untouched).
- [ ] 3.5 `openspec validate` (openspec CLI not installed locally; delta written against the `advertise-gpt6-max-output-tokens` change format).
