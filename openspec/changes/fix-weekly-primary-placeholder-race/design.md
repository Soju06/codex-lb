## Context

codex-lb supports accounts whose upstream usage payload reports the weekly
window in the `primary_window` slot rather than `secondary_window`. The
`normalize_weekly_only_rows` / `should_use_weekly_primary` path exists to
remap such a weekly `primary` row into the `secondary` slot so the 5h/7d
dashboard surfaces stay semantically consistent.

The remap decision is made by `_should_prefer_primary_row`. Originally it
decided the winner by comparing `recorded_at` first. Because the updater
writes the `primary` and `secondary` rows of a single fetch ~10 ms apart, and
the `secondary` no-data placeholder row is consistently written after the
primary, the placeholder won on most refresh cycles. The dashboard then read
the placeholder (`used_percent == 0.0`) as "100% remaining", causing the
weekly chart to jump to full.

## Goals / Non-Goals

**Goals:**

- Make the weekly-primary to secondary remap tiebreak data-aware so a no-data
  placeholder can never displace a real weekly primary row within the same
  refresh fetch.
- Preserve the pre-fix cross-fetch behavior: a genuinely later fetch (beyond
  the sibling margin) is more authoritative, so the newer row wins — a stale
  real weekly primary must not freeze the weekly value over a fresh placeholder
  from a later fetch.
- Apply the fix once in the shared `should_use_weekly_primary` /
  `_should_prefer_primary_row` path so account summaries, dashboard
  overview/projection aggregation, and account trends all benefit.
- Reuse the existing `SIBLING_FETCH_MARGIN_SECONDS` concept for same-fetch
  detection rather than introducing a new constant.

**Non-Goals:**

- Changing quota capacity, plan-type handling, or status derivation.
- Changing the upstream payload model or storage schema.
- Adding operator-facing settings or dashboard UI changes.
- Generalizing to an arbitrary N-window quota model.
- Representing a newer no-data placeholder as an explicit "unavailable" window
  (the newer fetch simply wins and is rendered per existing placeholder rules);
  that is a possible future enhancement, out of scope here.

## Decisions

### D1: Fetch ordering first, then data-aware precedence within a fetch

**Chosen:** In `_should_prefer_primary_row`, fetch ordering decides ONLY when
both rows carry `recorded_at` and their difference is strictly greater than
`SIBLING_FETCH_MARGIN_SECONDS` (5.0 s). When they are within the margin
(same fetch), or one/both timestamps are unavailable, fall through to the
data-aware tiebreak (real quota metadata beats a no-data placeholder), then
reset-at precedence, then the stable weekly-primary default.

**Rationale:** A genuinely later fetch is more authoritative about what
upstream currently reports, so the newer row must win cross-fetch (preserving
pre-fix behavior and avoiding the stale-freeze regression an unconditional
metadata-precedence rule would introduce). Within a fetch, a no-data
placeholder is the absence of a measurement, not 0% used, so it must never
displace a real weekly sample. Comparing metadata presence is a stable,
deterministic signal that does not depend on write ordering.

**Alternative considered:** Make metadata precedence unconditional. Rejected:
a stale real weekly primary from an older fetch would permanently beat a fresh
placeholder from a later fetch, freezing stale weekly usage when upstream
later drops the window.

### D2: Same-fetch margin suppresses the sub-second timestamp coin flip

**Chosen:** When both rows carry `recorded_at` and their difference is at most
`SIBLING_FETCH_MARGIN_SECONDS` (5.0 s), treat them as same-fetch and do NOT
let the sub-second difference decide the winner; fall through to the
data-aware tiebreak. The margin boundary is inclusive: a delta of exactly
`SIBLING_FETCH_MARGIN_SECONDS` is treated as same-fetch; a delta strictly
greater than it is a different fetch.

**Rationale:** The updater already acknowledges that same-fetch rows land
within milliseconds and that only a newer-by-more-than-the-margin sibling
proves a later fetch. Reusing the same constant keeps the two call sites
consistent.

**Alternative considered:** Widen the margin or remove the `recorded_at`
comparison entirely. Rejected: the comparison is still the correct signal for
two real rows from genuinely different fetches. Only the same-fetch sub-second
case is pathological.

### D3: Timestamp presence alone never decides the winner

**Chosen:** When only one (or neither) `recorded_at` is present, fetch
ordering cannot be determined, so the code falls through to the data-aware
tiebreak (metadata → reset-at → stable default) rather than letting timestamp
presence alone pick the winner.

**Rationale:** A timestamped no-data placeholder must not beat an untimestamped
real weekly primary. Letting "has a timestamp" decide would reintroduce the
original placeholder-wins bug for rows whose `recorded_at` is missing.

**Alternative considered:** Treat the timestamped row as "newer" when only one
timestamp is present. Rejected: that short-circuits to a placeholder beating a
real row and contradicts the data-aware intent.

### D4: Single shared fix point

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
  reset-at precedence and stable default already handle that case; covered by
  a regression test.
- **A genuinely fresher real secondary is mis-classified as same-fetch** ->
  Mitigation: the 5 s margin is far larger than intra-fetch skew (~10 ms) and
  far smaller than the 60 s refresh interval, so a real later fetch always
  exceeds it.
- **Placeholder classification is too narrow** -> Mitigation: define a
  no-data placeholder as missing BOTH a positive window duration AND a reset
  deadline; a row with a real reset_at but zero used_percent is still real.
- **Exactly-one-missing timestamp** -> Mitigation: fetch ordering is gated on
  BOTH timestamps being present; otherwise the data-aware tiebreak runs. A
  regression test locks down the untimestamped-real-primary-vs-timestamped-
  placeholder case.

## Migration Plan

1. Update `_should_prefer_primary_row` to gate fetch ordering on both
   timestamps, then apply the data-aware tiebreak within the same-fetch
   margin.
2. Add focused unit tests for the full tiebreak matrix (same-fetch both
   orders, cross-fetch both directions, exact margin, both non-real, partial
   metadata, exactly-one-missing timestamp, 5h guard).
3. Run strict OpenSpec validation and the touched module test suites.

Rollback strategy: code rollback only; no data migration to reverse.
