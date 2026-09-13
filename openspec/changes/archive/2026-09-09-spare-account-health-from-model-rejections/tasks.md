## 1. Match the rejection independently of the error code

- [x] 1.1 Add `is_model_scoped_upstream_rejection(message)` to `app/modules/proxy/helpers.py`, matching the exact entitlement message for any model after whitespace folding, and leave `_is_account_model_unsupported_error` (model- and code-scoped, used by the replay paths) unchanged.

## 2. Keep the rejection out of account health

- [x] 2.1 Add `_is_model_scoped_rejection` to `app/modules/proxy/_service/streaming/helpers.py`, requiring HTTP 400 whenever a status is known.
- [x] 2.2 Return the classified failure from `_handle_stream_error` before any health mutation when it matches, logging the skip.

## 3. Verification

- [x] 3.1 Assert no `record_error`/`record_errors`/`mark_rate_limit`/`mark_quota_exceeded`/`mark_permanent_failure` for both the `upstream_error` and `invalid_request_error` code shapes.
- [x] 3.2 Assert the failure is still classified `retryable_transient` so failover is unaffected.
- [x] 3.3 Negative controls: a genuine `upstream_error` with an unrelated message, the same message at a non-400 status, and a `rate_limit_exceeded` failure all keep their existing penalties.
- [x] 3.4 Run `uv run ruff check`, `uv run ty check`, and the unit suite.
