## Context

The timezone-aware Reports implementation introduced in PR #990 already
computes `window_days` from the selected local `start_date` and `end_date`.
It then converts local-midnight boundaries to UTC for repository filtering and
daily bucketing. A later average calculation incorrectly derives a second day
count from the UTC boundary dates, so offset changes can turn a two-day local
selection into a divisor of one or three.

## Goals / Non-Goals

**Goals:**

- Make both per-day summary averages use the existing inclusive local
  `window_days` value.
- Prove both affected Casablanca transitions and unchanged control zones at
  the `ReportsService` response seam.
- Preserve every repository input and every other report response field.

**Non-Goals:**

- Changing timezone resolution or invalid-timezone fallback.
- Changing UTC filter boundaries, repository queries, daily bucketing,
  comparison semantics, range limits, schemas, or frontend rendering.
- Refactoring unrelated Reports code.

## Decisions

### Reuse the existing local calendar window length

The service will divide both totals by `window_days`, the value already used
for range validation and previous-window sizing. Recomputing from UTC
boundaries is rejected because those boundaries represent elapsed filter
instants, not the number of selected local calendar dates.

### Cover the service contract with one parameterized regression

A repository double will return fixed totals of 60 cost units and 30 requests
for two selected local days. The regression will cover both Casablanca offset
changes plus UTC, a stable Casablanca offset, and invalid-zone UTC fallback.
A new route-level test is not added because the route delegates this
calculation unchanged and existing integration tests already cover
serialization, timezone boundaries, filters, totals, daily rows, comparison,
and range validation.

## Risks / Trade-offs

- **Risk: a narrow arithmetic fix could mask a boundary regression.** The
  implementation leaves all boundary construction and repository calls
  untouched, while focused existing integration tests are rerun.
- **Trade-off: the repository double does not exercise SQL.** SQL behavior is
  outside the defect and remains covered by the existing Reports API and
  repository suites.
