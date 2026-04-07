# Capacity-Weighted Routing

## Problem

The current `usage_weighted` routing strategy selects accounts by comparing **usage percentages** only. When a Pro account (50,400 secondary credits) and a Plus account (7,560 secondary credits) are both at 30% usage, they receive equal selection probability despite Pro having 6.67x more absolute remaining capacity. This causes Plus accounts to exhaust far earlier, shrinking the available pool and degrading overall system stability.

## Solution

Add a `capacity_weighted` routing strategy that selects accounts with **probability proportional to remaining absolute credits** (secondary window). This ensures traffic distribution matches actual capacity headroom, so all accounts approach exhaustion at roughly the same time regardless of plan tier.

## Changes

- Extend `AccountState` with `plan_type` and `capacity_credits` fields.
- Add `capacity_weighted` to `RoutingStrategy` literal type.
- Implement `_select_capacity_weighted()` using `random.choices()` weighted by remaining secondary credits.
- Populate capacity from `capacity_for_plan()` in `_state_from_account()` and `_state_for()`.
- Expand `PLAN_CAPACITY_CREDITS_*` dictionaries with enterprise/edu/free entries.
- Change the default routing strategy from `usage_weighted` to `capacity_weighted`.

## Impact

- All plan tiers (free, plus, pro, team, business, enterprise, edu) gain capacity-aware routing.
- Existing `usage_weighted` and `round_robin` strategies remain available and unchanged.
- Sticky session, error backoff, and quota enforcement logic are unaffected.
