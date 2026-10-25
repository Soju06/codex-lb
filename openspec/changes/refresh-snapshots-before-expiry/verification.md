# Verification

## Completed checks

- Seven focused scheduling and control cases passed against isolated SQLite,
  using the actual repository, scheduler and health-route code. The old schedule
  is retained as a negative control: it misses replacement at the early deadline
  and returns unavailable readiness at the old value's expiry boundary.
- A separate MariaDB 10.11 check exercised old and corrected publication schedules,
  due selection, successful replacement, failure backoff, stale values and
  generation/owner fencing.
- Scoped lint, formatting and application typing passed. Strict OpenSpec validation
  passed.
- Source review identified a missing successful scheduler/readiness regression,
  a midnight-dependent test clock, and an old-schedule control that left the
  producer idle. The tests now use a noon clock anchor and service both schedules
  at their actual deadlines. Four bounded collectors cross the old freshness
  boundary while collection is active; the early schedule finishes before that
  boundary. Both producers eventually publish successfully. The seven focused
  cases passed again using the current repository and scheduler source.

## Limits

The scheduling tests substitute collectors and advance a controlled clock. They
prove the scheduling and readiness transitions, not real collector latency or
sustained performance. These checks are not a full-suite or cross-engine acceptance
claim.

Deployment acceptance is recorded separately from these component checks. Source
review, an installed-but-stopped candidate, or focused test results alone do not
establish safe adoption or sustained freshness. Operational evidence is retained
outside the source tree.
