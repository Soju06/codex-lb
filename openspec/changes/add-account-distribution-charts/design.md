# Design

## Context

See proposal.md for motivation. Accounts already loads the complete inventory before filtering or pagination. Dashboard uses a shared DonutChart with theme-aware colors, hover/focus interaction and reduced-motion support, but its labels currently assume credit consumption.

## Goals / Non-Goals

**Goals:** reuse the shared rendering with a small explicit distribution mode; derive all statistics from AccountSummary data; preserve account selection and management behavior.

**Non-Goals:** additional API requests, stored counters, new settings, chart-driven filtering, or changing routing eligibility.

## Decisions

- Add a distribution presentation option to DonutChart: total caption, exact counts with percentages, no synthetic consumed category, neutral empty ring. Existing credit/remaining modes keep their behavior. This avoids duplicating the chart and interactive legend implementation.
- Group normalized plan strings and known raw statuses in an Accounts component. Unknown statuses use an explicit Unknown bucket, because the badge normalizer's fallback to Active would misrepresent inventory health.
- Use the complete accounts query as input, before search or pagination. The subtitle states this scope. Charts are present in all views and only when query data exists.
- Match status badge colors; keep plan categories deterministic and separate Pro variants. Stack chart and legend on narrow screens so translated labels and counts fit.

## Risks / Trade-offs

- Shared component changes could affect Dashboard → retain defaults and run existing donut and Dashboard tests.
- Rounded percentages can sum slightly above/below 100 → counts and center totals remain exact.
- More vertical content on mobile → two responsive cards with bounded scrollable legends; preserve all account actions below.
