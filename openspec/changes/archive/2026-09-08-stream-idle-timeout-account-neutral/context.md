Idle stream silence is a transport/read timeout, not an account fault.
`_stream_once` already failovers via `_RetryableStreamError(..., exclude_account=True)`.
The missing piece was `_handle_stream_error`: `stream_idle_timeout` was not in
the account-neutral set, so classification fell through to `record_error`.

Adding the code to `_is_account_neutral_error_code` is the same seam used by
process-network, `proxy_unavailable`, and compact input-too-large. This request
still excludes the idle account. Later independent requests may select it again
while it remains healthy.

Example: account A emits `response.failed` / `stream_idle_timeout` as the first
SSE event. The request moves to account B and succeeds. A's request log stays
`stream_idle_timeout`. A's `error_count` stays 0.
