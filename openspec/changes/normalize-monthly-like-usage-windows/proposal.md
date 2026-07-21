## Why

Upstream Team accounts can expose their only standard quota in the primary
slot with a duration near one calendar month rather than exactly 30 days. A
production variant reports `43800` minutes and may include a zero-duration
secondary placeholder. Exact `43200` checks leave that quota in the 5h slot,
so dashboard and routing semantics misclassify a 30-day window as short-term
pressure.

## What Changes

- Classify durations from 28 through 32 days as monthly-like instead of
  requiring exact equality with 43200 minutes.
- Ignore zero-duration, zero-usage placeholder windows before poll-time and
  live-ingest slot normalization.
- Promote authoritative monthly-like primary rows into monthly presentation
  even when stored plan metadata does not advertise a monthly capacity model.
- Preserve stale-plan protection: a meaningfully newer standard window
  supersedes an older monthly row.
- Treat monthly-like rows as long-window routing pressure and never as a 5h
  signal.

## Impact

- Team and other plans follow the observed upstream quota duration.
- Existing exact 30-day Free-account behavior remains supported.
- No setting, schema, endpoint, or wire-format change is introduced.

Fixes #1367
