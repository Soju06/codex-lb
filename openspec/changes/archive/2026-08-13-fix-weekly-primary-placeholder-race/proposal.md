## Why

The dashboard weekly (7d) usage chart repeatedly jumps to 100% remaining,
masking the real weekly usage that is actually climbing. On a live account the
weekly window is returned by upstream in the `primary_window` slot (with
`limit_window_seconds == 604800`) while `secondary_window` carries an empty
no-data placeholder (`window_minutes` falsy, `reset_at` null, `used_percent`
`0.0`, no credit metadata). codex-lb is supposed to remap that weekly
`primary` row into the `secondary` slot via `should_use_weekly_primary`, but
the tiebreak in `_should_prefer_primary_row` decides the winner by which row's
`recorded_at` is a few milliseconds later. Both rows are written in the same
refresh cycle ~10 ms apart, so the winner is effectively a coin flip per
refresh. When the no-data placeholder wins, the dashboard reads it as "0% used
= 100% remaining" and the chart spikes to full.

This is not a display-only bug: the same tiebreak feeds account usage panels,
account trend charts, and dashboard overview/depletion aggregation, so the
weekly quota is misreported everywhere it is surfaced.

## What Changes

- Make the weekly-primary to secondary remap tiebreak data-aware instead of
  timestamp-race-driven: a weekly `primary` row that carries real quota
  metadata (positive `window_minutes` and a `reset_at`) MUST win over a
  competing `secondary` row that carries no quota metadata, regardless of
  sub-second `recorded_at` ordering.
- Stop letting a sub-`SIBLING_FETCH_MARGIN_SECONDS` `recorded_at`
  difference decide the winner between rows written in the same refresh cycle;
  treat such rows as same-fetch and fall through to the data-quality tiebreaker.
- Apply the same data-aware tiebreak consistently to account-summary remap,
  dashboard overview/projection remap, and per-bucket trend remap (all currently
  call `should_use_weekly_primary`).
- Add regression coverage proving a no-data secondary placeholder can never
  displace a real weekly primary row, and that the dashboard weekly remaining
  percent tracks the real weekly usage instead of jumping to 100%.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: the weekly-primary to secondary remap tiebreak
  (`should_use_weekly_primary` / `normalize_weekly_only_rows`) becomes
  data-aware and no longer decides on a sub-sibling-fetch-margin `recorded_at`
  difference. This governs account summaries, dashboard overview/projection
  aggregation, and account usage trends.

## Impact

- Affected code: `app/core/usage/__init__.py`
  (`should_use_weekly_primary`, `_should_prefer_primary_row`), which is shared
  by `app/modules/accounts/mappers.py` (`_effective_usage_windows`,
  `_effective_usage_trend_buckets`) and `app/modules/dashboard/service.py`
  (`normalize_weekly_only_rows`, `_should_use_weekly_primary_history`).
- No database migration, schema change, API change, or configuration change is
  required. Existing stored rows are reinterpreted in place by the corrected
  tiebreak on the next read.
- No new `CODEX_LB_*` setting is introduced; the existing
  `SIBLING_FETCH_MARGIN_SECONDS` constant (already used by the updater for
  sibling-row freshness) is reused as the same-fetch threshold.
