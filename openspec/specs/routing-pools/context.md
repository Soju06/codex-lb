# Routing pools

`GET /api/pools` exists for one reader: an external router deciding which seat
to send the next task to. The dashboard already shows per-account gauges; a
router needs the one number per vendor budget that says whether the next
request can be spent there.

## Why the Fable pool is special

Anthropic meters Fable-class models against a dedicated weekly window,
separate from the overall weekly one. The balancer already reads that marker
(`additional_usage_history`, quota key `anthropic_fable_scoped_weekly`,
refreshed by the account pulse) and uses it for eligibility when it is fresh,
falling back to the overall-weekly heuristic when it is not. Until this
capability, `/api/accounts` exposed only the resulting boolean, so a caller
could see *that* an account was out of Fable but not *how far*.

The pool reads the same signal per account that the routing path reads, so the
pool and the balancer cannot disagree about a given account. `source` reports
which signal carried the pool as a whole: one fresh marker anywhere makes it
`scoped_marker`, because the accounts that have one are the accounts that
matter for the decision.

## Decisions

- **Headroom is the best account, not the sum.** A request is served by one
  account, so the spendable number is `max`, not the total. The mean is
  reported alongside it as `aggregateRemainingPercent` for trend reading.
- **Percentages are of the vendor window**, i.e. `100 - usedPercent`, never
  rescaled by the eligibility threshold. The threshold decides eligibility;
  mixing it into the percent would make two pools' numbers incomparable.
- **Rate-limited and quota-exceeded accounts stay in the arithmetic.** Their
  remaining percent is already near zero, which is the correct signal, and
  they recover at the window reset without operator action. Canceled, paused,
  deactivated and re-auth rows are excluded: no amount of waiting makes them
  spendable.
- **`accounts` counts everything, `eligibleAccounts` counts what was used.**
  An operator reading `5 accounts, 1 eligible` learns something the excluded
  rows would otherwise hide.
- **An absent window counts as full.** Unmetered providers (GLM, Kimi) report
  no window, and an Anthropic account with no weekly sample yet has a fresh
  window — the same reading the Fable eligibility heuristic already takes.
- **The endpoint is read-only and derived.** It re-uses the account summary
  service rather than re-querying usage, so there is one code path building
  windows and no second place to keep in sync. No schema change, no migration.

## The Fable reserve

`anthropic_fable_scoped_max_used_percent` defaults to 90 rather than 100.
Routed Fable traffic therefore stops one tenth of the window early, leaving
the remainder for interactive work that cannot be moved to another model. Set
it to 100 to restore the previous behavior of spending the window to the wall.

## Example

```json
{
  "generatedAt": "2026-09-19T21:00:00Z",
  "pools": [
    {
      "id": "anthropic-fable",
      "provider": "anthropic",
      "kind": "fable_scoped",
      "accounts": 5,
      "eligibleAccounts": 2,
      "headroomPercent": 13.0,
      "aggregateRemainingPercent": 53.0,
      "resetAt": "2026-09-23T11:00:00Z",
      "status": "low",
      "source": "scoped_marker"
    }
  ]
}
```

Normative requirements live in [spec.md](spec.md).
