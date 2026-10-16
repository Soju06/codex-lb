# Change Proposal: Align the compact main-path budget with Responses streams

## Why

`/responses/compact` speaks the same long-running upstream Responses protocol
as an ordinary Responses stream, but its default 180-second request budget
leaves only a 150-second upstream window once the settlement reserve
(`min(30, max(1, 0.2 * remaining), 0.5 * remaining)`) is taken. Slow but
valid compact turns therefore fail locally with `upstream_request_timeout`
before their upstream result arrives.

## What Changes

- raise the default `compact_request_budget_seconds` from 180 to 900 seconds,
  giving compaction an ~870-second upstream window (5.8x the former 150 s)
  while staying within the existing default `proxy_account_lease_ttl_seconds`
  (900 s) so the `account-lease-ttl-covers-compact-budget` invariant holds
  unchanged;
- make the core compact client apply the configured
  `compact_request_budget_seconds` as its total timeout when no per-request
  remaining-budget override is present, and the smaller of the two when both
  are; there is still no second compact timeout setting;
- keep the compact SSE idle deadline independent of the total budget: the
  per-request override when present, otherwise `stream_idle_timeout_seconds`.

The stale-lease reclaim TTL (`proxy_account_lease_ttl_seconds`,
`DEFAULT_LEASE_TTL_SECONDS`) is deliberately **not** changed: it is the leak
backstop for every `response_create` lease, not only compaction, and the 900 s
budget fits under it as-is.

## Impact

- Compaction: a valid long compaction gets up to ~870 s upstream instead of
  150 s. Explicit smaller dashboard/environment budgets and cancellation keep
  their current behaviour; the remaining-budget override can still shorten the
  upstream call.
- Automations (coupled consequence, acknowledged here): the scheduler's run
  reclaim window is `max(30, max(pinned, current budget) + 30)`
  (`automations` spec, "Scheduler is safe in multi-replica deployments"), and
  each automation compaction runs under the same budget. With the new default
  a claim killed without an orderly release (SIGKILL, OOM, rolling restart)
  becomes reclaimable after 930 s instead of 210 s, and an automation
  compaction may run for up to 900 s instead of 180 s. The derivation rule is
  unchanged; only the default value it reads moves.
- Admission control: the worst-case leaked `response_create` slot recovery
  time stays at the documented 900 s lease TTL (+60 s grace).
