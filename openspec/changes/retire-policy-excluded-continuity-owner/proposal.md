## Why

`retire-continuity-owner-on-the-request-path` retires a bridged thread's owner inside the
waiting request when that owner cannot return before the request's budget expires. Its entry
gate recognises one failure shape only: 502 `previous_response_owner_unavailable`, which the
connect path raises for `continuity_owner_unavailable` and `hard_affinity_saturated`.

A rate-limited owner does not always produce that shape. An account whose routing policy is
`burn_first` leaves the eligible pool once its window is exhausted, so
`_required_continuity_owner_failure` finds the owner in the runtime accounts but outside the
eligible ones and reports `continuity_owner_policy_conflict`. That surfaces as 503
"Required continuity owner is outside the eligible account policy", bypasses the retirement
gate, and the thread stays unusable until the owner's reset — hours later — while healthy
accounts sit idle. One production incident produced 83 such 503s in nine minutes, all for
threads owned by one account; the owner was `rate_limited` with a reset 3.7 hours away against a two-hour budget,
which is exactly the case the request-path retirement exists for.

## What Changes

- **Widen the entry gate, not the decision.** `retire_unavailable_continuity_owner` also accepts
  a `continuity_owner_policy_conflict` failure. Everything after the gate is unchanged: one
  attempt per request, a proxy-injected anchor only, no file pin, and a durable lookup naming the
  account being retired.
- **The repository still decides.** `retire_continuity_owner_if_unavailable` writes only when the
  owner's status is one of the unavailable statuses and its reset horizon falls after the
  request's deadline (or is absent). A healthy owner that a policy merely excludes is therefore
  never retired, and the turn keeps failing closed with the same 503.

No schema change, no new `CODEX_LB_*` setting, and no change to the error a client sees when
retirement does not apply.

## Impact

- Affected spec: `sticky-session-operations`
- Affected code: `app/modules/proxy/_service/http_bridge/helpers.py`,
  `app/modules/proxy/_service/http_bridge/streaming.py`
- Relates to #1707
