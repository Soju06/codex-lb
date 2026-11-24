# Precompute dashboard and report snapshots

## Why
Browser prefetching does not remove computation from HTTP requests. Dashboard
projection reads still aggregate history and simulate depletion on demand;
report cache expiry removes completed results and blocks the next caller on a
scan. Reloading therefore resembles a new measurement period, and independent
replicas do not share warmed results. Long report presets must not make the
request path pay for historical aggregation.

## What
Publish complete, durable read snapshots from bounded background workers.
Warm all dashboard ranges and the 7/30/90-day report presets for
canonical and known viewer timezones independently of page visits. Requests
only validate/authenticate/normalize and read the completed snapshot; a missing
variant may register inexpensive background interest, never perform aggregation.
Retain last-good values during refresh or failure and distinguish cold warming,
stale data, genuine missing observations, and unsupported historical coverage.
Measure and reduce recurring refresh work using existing permanent rollups and
reusable daily results rather than repeating whole-history scans per preset.

## Scope
Dashboard overview/projections, Reports/options/thread identity, background
ownership, persistence, and freshness presentation. Preserve billing/account
semantics, filtering, timezone boundaries, native proxy traffic and finite
admission limits. No new external service or required configuration step.
