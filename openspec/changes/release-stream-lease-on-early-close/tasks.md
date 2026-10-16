# Tasks: release-stream-lease-on-early-close

## 1. Implementation

- [x] 1.1 `app/modules/proxy/api.py` `_normalize_public_responses_stream`: close
  the inner stream before the native transport-failure `stream_incomplete` raise.
- [x] 1.2 `app/modules/proxy/api.py` `_stream_response_error_events`: close the
  inner stream in a `finally`.
- [x] 1.3 `app/modules/proxy/api.py` `_normalize_reasoning_summary_stream`: close
  the inner stream in a `finally`.

## 2. Regression coverage

- [x] 2.1 `tests/unit/test_proxy_utils.py::test_native_transport_failure_after_event_closes_the_upstream_stream`:
  drives the real `_normalize_public_responses_stream` / `_stream_response_error_events`
  / `_stream_with_retry` chain, with the upstream suspended at a `yield` when the
  native give-up raises. Asserts the account lease is released.

## 3. Verification

- [x] 3.1 `openspec validate release-stream-lease-on-early-close --strict`
