## Why

`GET /v1/usage` builds `upstream_limits[]` in `_build_aggregate_credit_limits`
from each account's latest sample per window, and `summarize_usage_window`
takes `min(reset_at)` across them. Expired samples are not filtered out
there. When an account stops reporting a window (on one deployment a free
account switched to a monthly-only window), its last 5h and 7d samples are
never rewritten, and their elapsed `reset_at` wins the `min()` from then on.

On a two-account deployment this produced `upstream_limits` with a 5h
`reset_at` of 2026-09-25 and a 7d `reset_at` of 2026-09-28, both read on
2026-10-14, even though the other account was refreshing every minute with
correct resets. Clients render this as a reset that is "due now" for weeks.

The rate-limit headers and the codex usage payload
(`app/modules/proxy/_service/rate_limit.py`) already treat an elapsed sample
as a reset window via `usage_core.expire_elapsed_window_rows`. The `/v1/usage`
aggregate never applied it.

## What Changes

- `_build_aggregate_credit_limits` runs `expire_elapsed_window_rows` over the
  primary, secondary and monthly rows after `normalize_weekly_only_rows`,
  the same order `rate_limit.py` uses.
- A window whose every sample has elapsed now has no reset and is omitted, the
  same path a missing `reset_at` already takes.

## Impact

- `upstream_limits[]` (and the legacy `limits[]` mirror) on `GET /v1/usage`.
- No schema, setting or API shape change.
