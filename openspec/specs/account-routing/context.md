# Account Routing Context

## Purpose

This context explains the **Routing Strategy** options exposed in the dashboard
(Settings -> Routing) so operators can pick a setting that matches their
volume profile, account topology, and tolerance for unusual upstream traffic
patterns. The normative requirements for individual strategies live in
[`spec.md`](./spec.md) and in the archived OpenSpec changes that introduced
each strategy.

The selector is implemented in
[`app/core/balancer/logic.py`](../../../app/core/balancer/logic.py) via
`select_account`. All strategies run **after** the eligibility, health-tier,
model-plan, quota, cooldown, circuit-breaker, and budget-safety gates have
filtered the candidate pool, so they only differ in how they pick from the
already-eligible set.

## Strategies

All values match the dashboard `Select` items in
[`frontend/src/features/settings/components/routing-settings.tsx`](../../../frontend/src/features/settings/components/routing-settings.tsx)
and the schema literal in
[`app/modules/settings/schemas.py`](../../../app/modules/settings/schemas.py).

| Strategy | Determinism | Selection rule | Typical use case |
| --- | --- | --- | --- |
| `capacity_weighted` (default) | Random, weighted | Pick with probability proportional to remaining secondary (weekly) credits. | Balanced fan-out across a pool; smooths load without concentrating traffic. |
| `relative_availability` | Random, weighted | Score = remaining secondary credits / seconds until secondary reset, raised to `relative_availability_power`. Top-K cutoff applied before weighted draw. | Same balance goal as `capacity_weighted`, but biases toward accounts whose credits would otherwise expire soon at reset. |
| `fill_first` | Deterministic | Highest primary 5h `used_percent` wins; ties broken by higher secondary `used_percent`, then by `account_id`. | Drain one account to completion before touching the next. Preserves freshest accounts for later cycles. |
| `sequential_drain` | Deterministic | Lowest `(reset_at, account_id)` sort key wins. | Operator-controlled draining order; predictable, low jitter. |
| `reset_drain` | Deterministic | Bucketed by reset-window proximity; drains the bucket closest to its own reset first. | Same intent as `sequential_drain` but reset-aware so you don't waste credits at the reset boundary. |
| `single_account` | Pinned | Always pick the dashboard-selected `singleAccountId`. | One-operator-one-account topology where pooling is undesired. |
| `usage_weighted` | Deterministic | Lowest pressure first. `usage_weighted_order` controls whether secondary pressure or primary pressure is ranked first. | Spread traffic to the freshest account; predictable fair-share behavior. |
| `round_robin` | Deterministic | Account with the oldest `last_used_at` wins; ties broken by `account_id`. | Even rotation; produces the most regular per-account request cadence. |

## Selection-pool semantics shared by every strategy

- Accounts in `REAUTH_REQUIRED`, `DEACTIVATED`, or `PAUSED` never enter the
  pool. `RATE_LIMITED` and `QUOTA_EXCEEDED` accounts are excluded until
  `reset_at` elapses (or until an explicit bypass for additional-quota gated
  models is active).
- Accounts in error backoff (`error_count >= 3`) are held out for an
  exponential window, then surfaced as a fallback if no fully available
  account exists and `allow_backoff_fallback` is on.
- The `prefer_earlier_reset_accounts` toggle and
  `prefer_earlier_reset_window` ("primary" 5h vs "secondary" weekly) layer
  on top of every strategy and can shift the chosen account toward one with
  a sooner quota reset.
- `relative_availability_power` and `relative_availability_top_k` only
  apply when `routing_strategy = relative_availability`. Power is validated
  positive, top-K must be an integer 1-20 per
  [`spec.md`](./spec.md).

## Account-safety guidance

This section answers the operator question raised in
[#1059](https://github.com/Soju06/codex-lb/issues/1059): which routing
strategy is safest for low-volume, compliant personal use, and which
strategies create traffic patterns that look unusual upstream.

| Goal | Recommended strategy | Why |
| --- | --- | --- |
| Minimize unusual upstream patterns; one operator, one or two accounts. | `single_account` | No fan-out at all. Traffic looks identical to using the account directly. |
| Conservative pool with predictable cadence per account. | `round_robin` | Even, deterministic rotation. No account gets a sudden burst relative to its peers. |
| Conservative pool that follows account headroom. | `usage_weighted` | Picks the freshest account, so per-account request rate stays low while the pool drains slowly. |
| Balanced default — pool absorbs variable load without concentrating it. | `capacity_weighted` *(default)* | Weighted by remaining credits, so heavier accounts attract more traffic in proportion to their headroom. |
| Reduce wasted reset credits on accounts that reset soon. | `relative_availability` | Same as `capacity_weighted` plus a soon-resetting bias. Still pool-wide. |
| Burn one account down before moving on. | `fill_first` / `sequential_drain` / `reset_drain` | Concentrates traffic on one account at a time. Highest per-account request rate of any strategy; most visible upstream as "one account is doing all the work". |

Strategies that concentrate traffic (`fill_first`, `sequential_drain`,
`reset_drain`) are not unsafe by themselves and are the right answer when an
operator deliberately wants to drain an account to its reset boundary. They
*do* produce the most visible per-account traffic spike, so prefer them for
batch or off-hours work rather than as the default for interactive use.

`single_account` is the closest match to native account behavior because
there is no balancer in front of the request: codex-lb still tracks usage and
applies the same eligibility gates, but every chargeable request goes through
the operator-selected account.

This guidance is **not** a recommendation to bypass any upstream policy.
codex-lb cannot and does not evade rate-limit decisions made by the upstream
account; every strategy still respects the canonical `QUOTA_EXCEEDED` /
`RATE_LIMITED` gates listed above.

## Related controls

The Routing card in the dashboard exposes several toggles that interact with
the chosen strategy:

- **Sticky threads** keeps related requests on the same account regardless
  of strategy. See [`sticky-session-operations`](../sticky-session-operations/spec.md).
- **Additional quota routing policies** override the strategy for traffic
  that maps to a specific model-quota pool (`inherit`, `normal`,
  `burn_first`, `preserve`).
- **Prefer earlier reset** biases the chosen strategy toward accounts whose
  primary or weekly window resets sooner.
- **Limit warm-up** is a separate post-reset confirmation endpoint and does
  not influence which strategy runs.

When changing strategy mid-flight, in-flight sticky requests stay pinned;
new requests use the new strategy on the next selection tick.

## Verification

To confirm which strategy is active and what it selected, watch the
balancer logs while making one request:

```bash
uv run codex-lb --log-level info
# Look for "select_account" log lines naming the routing_strategy and
# selected account_id.
```

The settings GET endpoint also returns the live value:

```bash
curl -s http://127.0.0.1:2455/api/settings | jq '.routingStrategy'
```

## History

Each strategy was introduced or refined through its own OpenSpec change. The
design rationale for each strategy lives in its own change folder:

- `openspec/changes/archive/2026-04-14-capacity-weighted-routing/` — default strategy.
- `openspec/changes/archive/2026-06-02-add-relative-availability-routing/` — soon-resetting bias.
- `openspec/changes/add-fill-first-routing-strategy/` — operator-driven drain ordering.
- `openspec/changes/add-drain-routing-modes/` — sequential and reset-aware drain.
- `openspec/changes/add-reset-window-routing/` — reset-window bucketing helpers.

See `openspec/changes/` and `openspec/changes/archive/` for the full list of
routing-related changes.
