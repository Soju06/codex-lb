# Design: weekly pace runway model

## Rationale for the model change

The card must answer four operator questions, in order:

1. How much is left? → `headroom_percent`, `headroom_credits`
2. When does it run out at the current pace? → `depletion_eta_hours`
3. Does the next reset save us? → `next_relief_in_hours`,
   `next_relief_credits`, and the relief-gated verdict (`runway_status`)
4. Who is burning it? → `top_api_keys[]`

These thresholds and the relief-gated verdict were validated by backtesting the
2026-08-16 incident: a headroom/ETA rule fired 21h (warn) and 5h (crit) before
user-visible failure, while the linear-schedule delta and `accounts.status`
gave no usable signal at any point. The same math already runs in the ops
watchdog (`codex_lb_quota_watch.py`); this change makes the dashboard card and
the alerting share one truth.

## Calculation details

- **Burn rate**: keep the per-account 6h EWMA (`_recent_burn_rate_credits_per_hour`)
  for the fleet forecast, but expose the fleet trailing 3h mean as
  `burn_rate_recent_credits_per_hour` — it is what the ETA headline uses.
  Rationale: 3h mean is responsive without single-hour jitter, and matched the
  incident timeline in backtest. EWMA remains an input to the reset-aware
  simulation.
- **ETA**: `headroom_credits / burn_rate_recent` (no burn → null). The
  reset-aware simulation (`_project_weekly_pool`) is retained and still
  produces `projected_depletion_hours` / `projected_minimum_remaining_credits`;
  the two answers differ when a reset lands first, which is exactly what the
  relief verdict presents.
- **Relief**: soonest `reset_at` among accounts ≥95% used (they return the
  most credits); `next_relief_credits` sums `used_percent/100 × capacity` for
  accounts whose reset falls within 1h of that soonest reset (reset-day
  clustering, same rule as the ops tooling). `reset_events[]` lists per-account
  `{at, credits_returned}` for the timeline bar, capped to the next 7 days.
- **Verdict** (`runway_status`):
  - `runs_dry`: ETA < time-to-relief (finite ETA)
  - `tight`: survives, but `eta − relief` is less than 24h or headroom is
    less than 12%
  - `safe`: otherwise
  Legacy `status` is derived for one release: `runs_dry→danger`,
  `tight→ahead`, `safe→on_track` (`behind` retired).
- **Censoring guard**: `saturated_account_count` (≥99.5%). When
  `saturated == account_count`, demand-derived numbers are floors; the card
  labels them as such.
- **Attribution**: per-key trailing 2h from `request_logs`: requests, billable
  tokens (input+output+reasoning), cached tokens, dominant model. No per-key
  credit estimate in v1 — credits-per-request varies ~5x by workload shape and
  a wrong number is worse than none; naming the key is the actionable part.
  Top 3 by requests + top 3 by billable tokens, merged, deduped.
- **Recommendation**:
  - First remedy: existing `throttle_to_percent` (already computed, now
    surfaced) — shown only when `runs_dry`.
  - `add_pro_accounts`: from trailing-7d fleet demand in quota-weeks
    (`Σ positive weekly burn / 50,400`) minus current fleet capacity in
    quota-weeks (`Σ full_credits / 50,400`), ceil,
    shown only when demand exceeds capacity AND (`runs_dry` or
    `saturated_account_count > 0`). This is stable across hours and matches
    the capacity-sizing methodology used for actual purchase decisions.

## Perf / loading

`weekly_credit_pace` is already computed inside `GET /api/dashboard/overview`;
the card currently waits for the projections query before painting in some
states. Frontend requirement: paint from the overview payload immediately,
treat `projections.weeklyCreditPace` as a refinement, and reserve layout with a
fixed-size skeleton so the card neither pops in late nor shifts.

## Attribution query cost

One aggregate over `request_logs` bounded to 2h (indexed on `requested_at`),
grouped by `api_key_id`, LIMIT'd. Runs inside the overview handler; measured
row counts at incident peak (~32K/h) keep this well under the endpoint budget.
If overview latency regresses, the fallback is moving attribution to the
projections endpoint — decided at review time with numbers, not preemptively.

## Wire compatibility

All current `WeeklyCreditPaceResponse` fields remain populated for one release
(the linear-schedule fields keep their current math). New fields are additive.
Frontend Zod schemas mark new fields optional so older backends parse.

## PR split

1. **Backend**: calc rewrite + schemas + attribution query + unit/integration
   tests. Reviewable without UI.
2. **Frontend**: card redesign + utils/schema updates + tests + before/after
   screenshots (repo rule for dashboard-visible PRs).
