# Quota Phase Planner

The quota phase planner is a small control loop for Codex rolling quota windows.
It is not a generic cron automation subsystem.

The planner has three responsibilities:

- keep cold accounts cold when a small request would start a bad five-hour phase;
- prefer active windows that are close to reset so remaining quota is used before it expires;
- forecast historical demand and record auditable shadow/suggest decisions for future warmup automation.

The default mode is intentionally low-friction: `shadow`, `prewarmEnabled=true`, `dryRun=true`, and
`allowSyntheticTraffic=false`. A fresh installation therefore gets phase-aware routing and audit rows without
having to answer setup questions and without sending background traffic. If forecast or usage data is stale,
missing, or uncertain, the planner degrades to soft costs and no-op decisions instead of blocking real requests.

## Modes

Planner settings live at `/api/quota-planner/settings`.

- `off`: planner costs and scheduler decisions are disabled.
- `shadow`: routing costs are active and scheduler decisions are written as skipped audit rows.
- `suggest`: scheduler decisions are written as planned rows for operator review.
- `auto`: may execute synthetic warmup traffic, but only after every safety gate passes.

Synthetic traffic is gated by `allowSyntheticTraffic` and `dryRun`. Even in `auto`, the executor skips instead of
sending traffic unless the account is active/cold, daily count and credit budgets remain, usage policy is clean, and
the `(account, model)` warmup effect is known. Manual warm-now can force an explicit probe for operators, but the
default scheduler does not assume one model starts every relevant window.

## Routing Costs

The request path reads cached planner settings and builds request-scoped routing costs from current account state.
Hard eligibility still wins first: paused, deactivated, rate-limited, exhausted, and cooldown accounts remain blocked.
After that:

- cold accounts outside work/prewarm bands receive a high cost;
- cold accounts inside work or prewarm bands receive a smaller cost;
- active windows with less than one hour before reset receive a negative cost, so routing drains them first;
- accounts with unknown usage receive a small uncertainty cost.

These costs are passed into the existing `usage_weighted`, `round_robin`, `capacity_weighted`, and sticky fallback
selectors instead of being persisted on account state.

## Scheduler

The scheduler runs through the existing durable leader-election path every `CODEX_LB_QUOTA_PLANNER_TICK_SECONDS`
seconds, defaulting to five minutes. It can be disabled with `CODEX_LB_QUOTA_PLANNER_SCHEDULER_ENABLED=false`.

Each tick:

- loads planner settings;
- skips work when mode is `off`;
- builds account window state from the latest primary and secondary usage rows;
- aggregates the last 28 days of real request logs into 15-minute demand bins;
- builds a deterministic 36-hour forecast from weekday/hour history, recent usage, and the work calendar prior;
- simulates the current pool against the forecast;
- scores candidate starts against the forecast peak, work-block demand, warmup cost, and reset synchronization penalty;
- considers peak starts (`peak - 5h` plus nearby stagger slots), work-block offsets, demand/capacity crossings, and
  active-account reset spacing;
- emits reserve/warmup decisions during the configured prewarm band only when net expected gain beats the threshold;
- chooses no-op instead of warming when flat/low demand, stale/uncertain state, or policy costs make every candidate
  non-positive;
- writes a no-op decision when there is nothing useful to do, so ticks remain auditable;
- writes decisions to `quota_planner_decisions` with a per-account `warmup_cycle` idempotency key;
- in `auto`, passes due warmup actions through the same gated executor used by the admin API.

The planner does not warm every idle account once per cycle. A cycle is only an idempotency and de-synchronization
guard: candidates still need positive peak-aligned value. Multiple cold accounts are selected greedily so each
accepted reset is included in the next account's synchronization penalty, which staggers windows across the prewarm
band instead of creating one shared reset cliff.

The simulator is deliberately simple and explainable. It treats each account window as a finite capacity bucket,
routes forecast demand into active or planned windows, and scores unmet demand, wasted capacity, cold starts, and
synchronized reset times. This is a planner/control-loop primitive, not an ML optimizer.

## Data Model

The planner adds:

- `quota_planner_settings`: singleton settings row;
- `quota_planner_decisions`: audit log for reserve/warmup/no-op decisions;
- `quota_window_observations`: observed reset/remaining state for future model-effect measurement;
- `request_logs.request_kind`: request class such as `real` or `warmup`.

Historical demand aggregation is exposed in the repository by 15-minute buckets across account, API key, model,
reasoning effort, request kind, status, and token/cost totals.

## Dashboard

The Settings page includes a quota planner section with:

- mode and dry-run controls;
- working days/hours and prewarm lead configuration;
- forecast quantile and gain/budget knobs;
- current 36-hour forecast/simulation summary;
- recent decision timeline with action status, scheduled time, target peak, expected gain/cost, skip/no-op reason, and
  `warmup_cycle` where available.

The UI mirrors the safe defaults. Enabling synthetic traffic is visible and explicit, and warm-now/cancel actions call
server-side gates rather than bypassing policy.

## Warmup Accounting And Effects

Warmup execution uses the normal request accounting surfaces:

- optional API-key usage reservation/finalization when an API key is supplied;
- `request_logs.request_kind = "warmup"` for success and failure logs;
- `quota_window_observations.source = "warmup_probe"` to record observed reset/remaining state and confidence;
- daily warmup count and credit budgets before traffic is sent.

The executor records skipped decisions with specific reasons such as `synthetic_traffic_disabled`,
`dry_run_enabled`, `warmup_effect_unknown`, `account_window_already_active`, or budget exhaustion. These skips are
intentional: planner uncertainty must not block real user work or silently burn account quota.

## API

- `GET /api/quota-planner/settings`
- `PUT /api/quota-planner/settings`
- `GET /api/quota-planner/decisions?limit=50`
- `GET /api/quota-planner/forecast?horizonHours=36`
- `POST /api/quota-planner/warm-now`
- `POST /api/quota-planner/decisions/{decisionId}/cancel`

The routes use dashboard session authentication and write settings changes to the audit log.

Decision responses include `details` parsed from the planner audit JSON when available. Current scheduler details
include `target_peak_at`, `expected_gain`, `scenario_gain`, `expected_cost`, `net_score`, `warmup_cycle`,
`scheduled_at`, `skip_reason`, `noop_reason`, and `unmet_demand`. Older rows may have `details = null`.
