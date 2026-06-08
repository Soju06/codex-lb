## Why

The API key overview cards now surface usage totals and per-key breakdowns from
`usageSummary` in the API key list payload. Those totals are built from all
non-warmup request logs, so labeling them as "7-day" is incorrect and misleading.

## What Changes

- Update dashboard copy for API key overview usage cards and breakdown panels to
  identify the totals as lifetime aggregates.
- Keep sorting, percentages, and rendering behavior unchanged.
- Clarify frontend-architecture requirements for the dashboard-facing interpretation
  of `usageSummary`.
