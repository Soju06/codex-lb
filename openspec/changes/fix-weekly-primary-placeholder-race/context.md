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
the `secondary` slot so the dashboard shows real weekly usage, but the old
`_should_prefer_primary_row` decided the winner with:

```python
if primary_recorded_at is not None and secondary_recorded_at is not None:
    if primary_recorded_at != secondary_recorded_at:
        return primary_recorded_at > secondary_recorded_at
```

Because the secondary placeholder is written a few milliseconds after the
primary row, it won on most cycles and the dashboard read the placeholder as
"0% used = 100% remaining". Reproduced against the live database with the app's
own `_effective_usage_windows`: the latest primary row reports
`used_percent=74.0` (`window_minutes=10080`, real `reset_at`), the latest
secondary placeholder reports `used_percent=0.0` (`window_minutes=0`,
`reset_at=None`), and `should_use_weekly_primary` returned `False`, so the
dashboard showed 100% remaining instead of the true 26%.

Over a six-hour window the computed weekly remaining jumped to 100% at least
eleven times, interleaved with the correct lower value, exactly matching the
"jumps to 100%" symptom.

## Decision rationale

The tiebreak is layered:

1. **Fetch ordering first, but only when provable.** A genuinely later fetch
   (both `recorded_at` present and differing by strictly more than
   `SIBLING_FETCH_MARGIN_SECONDS`, 5.0 s) is more authoritative about what
   upstream currently reports, so the newer row wins. This preserves the
   pre-fix cross-fetch behavior and avoids the stale-freeze regression an
   unconditional metadata-precedence rule would introduce.
2. **Data-aware precedence within a fetch.** When rows are in the same fetch
   (delta at most the margin), or one/both timestamps are unavailable (so
   fetch ordering cannot be determined), a row carrying real quota metadata
   (positive `window_minutes` AND non-null `reset_at`) wins over a no-data
   placeholder. A no-data placeholder is the absence of a measurement, not 0%
   used, so it must never displace a real weekly sample. Critically, timestamp
   presence alone never decides the winner — a timestamped no-data placeholder
   must not beat an untimestamped real weekly primary.
3. **Reset-at precedence, then the stable weekly-primary default.** When both
   rows are real or both are placeholders (same fetch / indeterminate
   ordering), reset-at precedence applies; failing that, the stable weekly-
   primary default wins to keep weekly-only semantics deterministic.

`SIBLING_FETCH_MARGIN_SECONDS` (5.0 s) is the same-fetch threshold shared by
this tiebreak and the usage updater's sibling-row freshness logic, defined once
in `app/core/usage/__init__.py`. (Other modules still keep their own private
5.0 s constants for unrelated sibling logic; consolidating those is out of
scope for this change.)

## Constraints and failure modes

- A real secondary weekly row that genuinely supersedes a stale weekly primary
  (written in a later fetch, beyond the 5 s margin) still wins as today.
- Two same-fetch real rows are resolved by reset-at precedence and the stable
  default; they do not flip on a sub-second `recorded_at` difference.
- Exactly-one-missing `recorded_at` does NOT decide the winner: the data-aware
  tiebreak runs instead, so a timestamped placeholder cannot beat an
  untimestamped real row.
- Monthly-only normalization (`limit_window_seconds == 2592000` primary, no
  secondary) is unaffected: that path is handled by
  `normalize_rate_limit_windows` before this tiebreak runs.
- The 5h (`window_minutes == 300`) path is structurally unaffected:
  `should_use_weekly_primary` returns `False` immediately for non-weekly
  primary windows, so the tiebreak never runs for 5h.
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
~13 ms newer), so the dashboard weekly remaining is 100%.

After the fix: both timestamps are present and the delta (~13 ms) is within
the 5 s margin, so the data-aware tiebreak runs; the primary row carries real
quota metadata (positive `window_minutes` and a `reset_at`) while the
secondary row is a no-data placeholder, so the weekly primary row wins and the
dashboard weekly remaining is 26% (matching `100 - 74`).

## Operational notes

No operator action is required. After deployment, the next dashboard read or
background refresh read repairs the displayed value in place. No setting is
added.
