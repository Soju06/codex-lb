## Why

Upstream `server_is_overloaded` is an admission rejection scoped to the
account: upstream refuses to start a new response for that account while the
account's already-admitted streams keep flowing. On a production pool during an
upstream capacity incident, 7 of 14 accounts were rejected on 20–70 % of their
fresh admissions for hours while the other 7 saw none — and each rejection took
30–90 s to arrive, so every fresh request routed to a rejected account paid
that wait before failover could even begin. Client-visible first-token latency
roughly doubled.

The existing account-health machinery never latched. The rejected accounts
kept succeeding on bridge reuse and sticky continuity (those sessions are
already admitted), and every success zeroes `RuntimeState.error_count`, so the
generic transient error backoff (three errors) and the drain tier (two errors
in 60 s) reset between rejections. A live probe of the balancer runtime showed
`error_count=0, health_tier=HEALTHY` for an account that had returned 123
overload rejections in the previous two hours. Fresh selection therefore kept
feeding the rejected accounts at their normal capacity weight.

## What Changes

- Each `server_is_overloaded` / `overloaded_error` rejection recorded through
  `_handle_stream_error` (the single funnel every transport uses for
  stream-error account health) also feeds a replica-local sliding window on the
  account's runtime state. The window is independent of `error_count` and is
  not reset by successes.
- When three rejections land inside 120 s, the account is deprioritized for
  fresh (unbound) selection for a bounded, exponentially growing interval
  (60 s, 120 s, …, capped at 600 s; the level decays after 30 quiet minutes).
  A trip while already deprioritized extends the deadline, never shortens it.
- Deprioritization is soft and strategy-agnostic: selection first runs over
  the overload-free candidates and, when the configured strategy/budget gates
  select none of them, runs again over the full pool exactly as before — so it
  can never produce `No available accounts` or a spurious account-cap error,
  and cap detection still sees the untouched cap-filtered pool.
- It applies wherever a NEW account is chosen: the unbound path and the sticky
  path's fresh binding / reallocation / fallback pick (a previously unseen
  session or prompt-cache key is a fresh upstream admission). An established
  sticky owner, continuity owner, or hard-affinity owner is never touched.
- HTTP-status failures whose body carries an overload code keep that code
  through the same-account retry loop (they used to collapse to
  `server_error`), so the post-exhaustion health write counts as a rejection.
  Observations are taken exactly where account health is written, so they
  inherit the existing settlement-before-health ordering; a same-account
  retry that then succeeds writes no health and is therefore not counted
  (known limit, shared with `error_count`).
- Recovery-probe reservation uses the same pool the selection ran over, so an
  older due probe skipped by the overload pass cannot invalidate the match. Sticky, continuity-owner, and
  hard-affinity selection are untouched, so warm sessions on the account keep
  flowing and hard-pinned sessions are never denied because of this signal.
- Engaging the backoff is logged with the level and duration.
- Failure classification, the failover decision, the generic `record_error`
  penalty, and the client-visible status and body are unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: overload rejections deprioritize the account for fresh
  selection through a replica-local window that successes do not reset.

## Impact

- `app/modules/proxy/_load_balancer/types.py`: four runtime fields.
- `app/modules/proxy/_load_balancer/overload_backoff.py` (new): window,
  trip/decay math, the soft candidate filter.
- `app/modules/proxy/_service/streaming/helpers.py`: the hook after the
  transient penalty in `_handle_stream_error`.
- `app/modules/proxy/_load_balancer/unbound_selection.py`: overload-free
  first pass with full-pool fallback on the fresh-selection path.
- `app/modules/proxy/_load_balancer/sticky_selection.py`: the same two-pass
  pick where the sticky path chooses a new account; `load_balancer.py`
  forwards the runtime map (one line).
- `app/modules/proxy/_service/streaming/retry.py`: overload codes survive
  HTTP-status retry aggregation.
- No change to `service.py`, settings, schema, or API.
  Thresholds are fixed constants, matching the existing drain/probe
  thresholds.
