# Weekly-primary remap tiebreak

## Purpose and scope

This change fixes a display- and routing-relevant correctness bug in how
codex-lb picks between a weekly window reported in the `primary` slot and a
competing `secondary`-slot row. It does not change quota capacity, plan types,
status derivation, or storage schema.

## Incident shape

A live Plus account's upstream `/wham/usage` response reports the weekly window
in `primary_window` (`limit_window_seconds == 604800`, a real `reset_at`,
`used_percent` climbing toward 100) and an empty `secondary_window`
placeholder (`used_percent == 0.0`, no `limit_window_seconds`, no `reset_at`,
no credit metadata).

The usage updater persists both rows within ~10 ms of each other in the same
refresh cycle (`updater.py` writes `primary` then `secondary`).
`should_use_weekly_primary` is supposed to move the weekly `primary` row into
the `secondary` slot so the dashboard shows real weekly usage, but
`_should_prefer_primary_row` decides the winner with:

```python
if primary_recorded_at is not None and secondary_recorded_at is not None:
    if primary_recorded_at != secondary_recorded_at:
        return primary_recorded_at > secondary_recorded_at
```

Because the secondary placeholder is written a few microseconds after the
primary row, it wins on most cycles and the dashboard reads the placeholder as
"0% used = 100% remaining". Reproduced against the live database with the app's
own `_effective_usage_windows`: the latest primary row reports
`used_percent=74.0` (`window_minutes=10080`, real `reset_at`), the latest
secondary placeholder reports `used_percent=0.0` (`window_minutes=0`,
`reset_at=None`), and `should_use_weekly_primary` returns `False`, so the
dashboard shows 100% remaining instead of the true 26%.

Over a six-hour window the computed weekly remaining jumped to 100% at least
eleven times, interleaved with the correct lower value, exactly matching the
"jumps to 100%" symptom.

## Decision rationale

The tiebreak must be data-aware, not timestamp-race-driven. A no-data
placeholder row (no positive window duration, no reset deadline, no credit
metadata) carries no quota information and therefore can never represent "0%
used". It must never displace a weekly `primary` row that carries real quota
metadata, regardless of `recorded_at` ordering.

The `recorded_at` comparison is retained only as a tiebreaker between two rows
that both carry real quota metadata AND were not written in the same refresh
cycle. Rows written within `_SIBLING_FETCH_MARGIN_SECONDS` (5.0 s, already
defined and used by the updater to detect same-fetch siblings) are treated as
same-fetch, so a sub-second timestamp difference can no longer flip the winner.

## Constraints and failure modes

- A real secondary weekly row that genuinely supersedes a stale weekly primary
  row (written in a later fetch, beyond the sibling margin) still wins as today.
- Two same-fetch rows that both carry real quota metadata fall back to the
  existing reset-at / recorded-at precedence, preserving current behavior for
  ambiguous-but-real samples.
- Monthly-only normalization (`limit_window_seconds == 2592000` primary, no
  secondary) is unaffected: that path is handled by
  `normalize_rate_limit_windows` before this tiebreak runs.
- No migration is needed; the corrected tiebreak reinterprets existing stored
  rows on the next read.
- The display symptom (donut/account panel/trend) is fixed entirely by the
  shared backend tiebreak; no frontend change is required.

## Concrete example

Latest stored rows for an account:

| window     | used_percent | window_minutes | reset_at     | recorded_at            |
|------------|-------------:|---------------:|-------------:|------------------------|
| primary    |         74.0 |          10080 | 1784780169   | 2026-07-17 09:01:59.253|
| secondary  |          0.0 |              0 | None         | 2026-07-17 09:01:59.266|

Before the fix: `should_use_weekly_primary` returns `False` (secondary is
~13 us newer), so the dashboard weekly remaining is 100%.

After the fix: the primary row carries real quota metadata (positive
`window_minutes` and a `reset_at`) while the secondary row is a no-data
placeholder, so the weekly primary row wins and the dashboard weekly remaining
is 26% (matching `100 - 74`).

## Operational notes

No operator action is required. After deployment, the next dashboard read or
background refresh read repairs the displayed value in place. No setting is
added.
