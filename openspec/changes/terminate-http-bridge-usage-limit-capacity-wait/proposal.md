## Why

The `responses-api-compat` spec already requires `usage_limit_reached`
selection failures to be terminal on the HTTP-bridge path, and bridge session
creation does raise the structured `429` without a recovery sleep. The bridge
streaming retry loops then re-derive a recovery wait from the error *message*:
whenever the exhausted pool's earliest reset is known, the selector's message is
`Rate limit exceeded. Try again in Ns` (capped at 300 s), which matches the
account-capacity recovery-hint pattern. The loop therefore emits
`codex.keepalive waiting_for_account_capacity` frames (or stalls silently when
HTTP errors are propagated) and retries session creation until the bridge
request budget (default 7200 s) is exhausted before returning the very same
`429`. Only the no-reset-known message `Usage limit reached` is terminal today.

## What Changes

- `_http_bridge_account_capacity_wait_seconds` returns no wait for
  `usage_limit_reached`, keyed on the structured error code rather than the
  message, so every bridge session-creation and submit retry loop reports the
  `429` immediately with `error.resets_at` intact.
- Client-visible: HTTP-bridge clients whose pool is exhausted with a known
  reset now receive the immediate `429 usage_limit_reached` (no `Retry-After`,
  which stays reserved for local overload codes) instead of up to a full
  request budget of capacity-wait keepalives followed by the same `429`.
- Unchanged: local caps (`account_stream_cap`, `account_response_create_cap`,
  `api_key_stream_fair_share`), `response_create_gate_timeout`, workspace
  spend-cap and upstream `rate_limit_exceeded` hints keep waiting within the
  bridge request budget; the post-submit `response.failed usage_limit_reached`
  retry path is untouched. The shared `_account_selection_recovery_sleep_seconds_from_message`
  helper is not changed, so the SSE and WebSocket paths keep their own guards.
- No overflow or fallback routing behaviour is introduced; this is the
  standalone D4 fix tracked under #2123.

## Impact

- `app/modules/proxy/_service/http_bridge/streaming.py` (helper module; no
  architecture ceiling touched, `http_bridge/mixin.py` unchanged).
- `responses-api-compat` requirement "Pool usage exhaustion is reported as a
  usage-limit error" gains an explicit bridge-retry-loop scenario.
