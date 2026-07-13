## Why

Recent Codex clients can show earned usage limit reset credits in `/usage` and
offer an action to redeem one. codex-lb currently mirrors usage windows but
drops the upstream reset-credit summary and has no endpoint for the redemption
request, so Codex can tell the user resets exist without being able to consume
one through codex-lb.

## What Changes

- Preserve upstream `rate_limit_reset_credits.available_count` on the
  `/api/codex/usage` response for authenticated ChatGPT callers.
- Add a Codex-compatible reset redemption endpoint at
  `/api/codex/rate-limit-reset-credits/consume`.
- Add a dashboard account detail Reset action that consumes one upstream reset
  credit for the selected account after operator confirmation.
- Forward redemption to upstream using the caller's ChatGPT bearer token,
  `chatgpt-account-id`, existing upstream proxy routing, and the caller-provided
  `redeem_request_id`.
- After a successful or idempotently successful reset, force-refresh codex-lb's
  persisted usage snapshot for the matching account.

## Impact

- Affects Codex usage/read and reset-credit consume flows.
- Does not add local reset-credit accounting; upstream remains the source of
  truth.
- Adds regression coverage for response shape, authentication, upstream
  forwarding, and local post-reset refresh.
