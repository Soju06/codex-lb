# Fill-First Routing Strategy

## Problem

Operators running codex-lb against a small pool of ChatGPT accounts sometimes
prefer to **drive one account at a time** instead of spreading load across the
pool. The existing `capacity_weighted` (default) and `usage_weighted`
strategies always touch every healthy account, which:

- spreads upstream prompt-cache locality thinly across accounts,
- complicates per-account cost attribution and debugging when only one or two
  accounts are saturating,
- defeats the natural "drain one 5h primary window before starting the next"
  workflow that operators with overlapping reset cadences rely on.

`round_robin` is not a substitute: it rotates regardless of usage and never
stays on a single account long enough to take advantage of upstream caching.

## Solution

Add a fourth routing strategy, `fill_first`, that **deterministically picks
the eligible account with the highest primary 5h `used_percent`**. When
two or more candidates share the same primary `used_percent`, the
account with the **higher** secondary (weekly) `used_percent` is
preferred — i.e. the one with the least remaining weekly capacity is
drained first and the freshest one is preserved for later. `account_id`
ascending is the final stable tiebreaker. The "fill" behavior emerges
naturally from the existing pool gating:

- An account stays selected while it remains the highest-usage candidate.
- Its primary `used_percent` rises with traffic until it falls out of the
  effective pool (rate-limited, quota-exceeded, cooldown, or
  `health_tier == DRAINING`), at which point the next-highest account is
  picked.
- By the time later accounts saturate, the first account's 5h window has
  typically reset and can re-enter the pool as fresh capacity.

The strategy does **not** introduce new randomness, new sticky state, or new
bypasses around health tiers or error backoff — it plugs into the same
`effective_pool` ladder (`healthy → probing → draining → all available`) used
by every other strategy.

## Changes

- Extend the `RoutingStrategy` literal in `app/core/balancer/logic.py` to
  include `"fill_first"`.
- Implement a pure helper `_select_fill_first(pool)` that returns
  `min(pool, key=(-(used_percent or 0.0), -(secondary_used_percent or 0.0), account_id))`.
- Add a `fill_first` branch to `select_account()` dispatch that respects
  `prefer_earlier_reset` the same way `capacity_weighted` does.
- Widen the Pydantic regex in `app/modules/settings/schemas.py`
  (`DashboardSettingsResponse` and `DashboardSettingsUpdateRequest`) to
  accept `fill_first`.
- Frontend: extend `RoutingStrategySchema`, the routing-settings dropdown,
  `ROUTING_LABELS`, the status-bar label switch, and mock factories /
  handlers / tests.
- Update the backend mocks/factories used by tests where a routing-strategy
  literal is referenced.
- No alembic migration is required: `dashboard_settings.routing_strategy` is
  a free-form `String` column (`app/db/models.py:269`) validated only at the
  Pydantic layer, so the existing column accepts the new value as-is.

## Impact

- Operators gain a deterministic single-account-at-a-time routing mode for
  cache locality and predictable cost attribution.
- Existing `usage_weighted`, `round_robin`, and `capacity_weighted`
  strategies remain available and unchanged.
- Default routing strategy stays `capacity_weighted`. No persisted rows are
  rewritten.
- Sticky session, error backoff, quota cooldown, retry-after semantics, and
  health-tier transitions are unaffected.
