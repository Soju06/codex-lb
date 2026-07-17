## Context

codex-lb supports accounts whose upstream usage payload reports the weekly
window in the `primary_window` slot rather than `secondary_window`. The
`normalize_weekly_only_rows` / `should_use_weekly_primary` path exists to
remap such a weekly `primary` row into the `secondary` slot so the 5h/7d
dashboard surfaces stay semantically consistent.

The remap decision is currently made by `_should_prefer_primary_row`, which
compares `recorded_at` timestamps first. Because the updater writes the
`primary` and `secondary` rows of a single fetch ~10 ms apart, and the
`secondary` placeholder row is consistently written after the primary, the
placeholder wins on most refresh cycles. The dashboard then reads the
placeholder (`used_percent == 0.0`) as "100% remaining", causing the weekly
chart to jump to full.

## Goals / Non-Goals

**Goals:**

- Make the weekly-primary to secondary remap tiebreak data-aware so a no-data
  placeholder can never displace a real weekly primary row.
- Apply the fix once in the shared `should_use_weekly_primary` /
  `_should_prefer_primary_row` path so account summaries, dashboard
  overview/projection aggregation, and account trends all benefit.
- Reuse the existing `_SIBLING_FETCH_MARGIN_SECONDS` concept for same-fetch
  detection rather than introducing a new constant.

**Non-Goals:**

- Changing quota capacity, plan-type handling, or status derivation.
- Changing the upstream payload model or storage schema.
- Adding operator-facing settings or dashboard UI changes.
- Generalizing to an arbitrary N-window quota model.

## Decisions

### D1: Data-aware tiebreak before timestamp comparison

**Chosen:** In `_should_prefer_primary_row`, classify each row as carrying
real quota metadata (positive `window_minutes` AND a non-null `reset_at`)
versus a no-data placeholder. A real weekly primary row MUST win over a no-data
secondary row (and vice versa) before any `recorded_at` comparison runs.

**Rationale:** A no-data placeholder (`window_minutes` falsy, `reset_at`
null, `used_percent` 0, no credit metadata) is not a measurement of "0% used";
it is the absence of a measurement. Letting it represent 100% remaining is the
bug. Comparing metadata presence is a stable, deterministic signal that does
not depend on write ordering.

**Alternative considered:** Track the placeholder at write time and skip
persisting no-data secondary rows. Rejected because it changes stored history
semantics, requires a migration for existing rows, and the tiebreak is still
needed for already-stored data and for two-real-row ambiguity.

### D2: Same-fetch margin suppresses the timestamp coin flip

**Chosen:** When both rows carry real quota metadata and their `recorded_at`
values differ by less than `_SIBLING_FETCH_MARGIN_SECONDS` (5.0 s, already
defined in `updater.py` for sibling-row detection), treat them as same-fetch
and fall through to the existing reset-at precedence (and the stable default)
instead of letting the sub-second difference decide.

**Rationale:** The updater already acknowledges that same-fetch rows land
within milliseconds and that only a newer-by-more-than-the-margin sibling proves
a later fetch. Reusing the same constant keeps the two call sites consistent.

**Alternative considered:** Widen the margin or remove the `recorded_at`
comparison entirely. Rejected: the comparison is still the correct signal for
two real rows from genuinely different fetches (e.g. a stale weekly primary
superseded by a fresher real secondary). Only the same-fetch sub-second case is
pathological.

### D3: Single shared fix point

**Chosen:** Apply the fix in `should_use_weekly_primary` /
`_should_prefer_primary_row` (`app/core/usage/__init__.py`). All four
consumers (account-summary `_effective_usage_windows`, trend
`_effective_usage_trend_buckets`, dashboard `normalize_weekly_only_rows`,
and dashboard projection `_should_use_weekly_primary_history`) already call
this path, so a single change repairs every surface.

**Rationale:** Minimizes blast radius and keeps the four call sites from
diverging.

**Alternative considered:** Patch each consumer independently. Rejected because
it duplicates the data-aware logic and risks drift.

## Risks / Trade-offs

- **Two real same-fetch rows become order-independent** -> Mitigation: the
  reset-at precedence and stable default already handle that case; add a
  regression covering two real weekly rows in the same fetch.
- **A genuinely fresher real secondary is mis-classified as same-fetch** ->
  Mitigation: the 5 s margin is far larger than intra-fetch skew (~10 ms) and
  far smaller than the 60 s refresh interval, so a real later fetch always
  exceeds it.
- **Placeholder classification is too narrow** -> Mitigation: define a
  no-data placeholder as missing BOTH a positive window duration AND a reset
  deadline; a row with a real reset_at but zero used_percent is still real.

## Migration Plan

1. Update `_should_prefer_primary_row` to add the data-aware classification and
   same-fetch margin check.
2. Add focused unit tests for the new tiebreak matrix.
3. Run strict OpenSpec validation and the touched module test suites.

Rollback strategy: code rollback only; no data migration to reverse.
