## Context

`AccountsService.list_accounts()` already loads the latest primary, secondary,
and monthly `UsageHistory` rows before building `AccountSummary`. The fleet
mapper consumes those summaries, but the usage rows' `recorded_at` values are
not currently carried through that projection. See `proposal.md` for the
consumer-facing problem.

The existing `/api/accounts` response is a separate public contract and must
not gain a new field as an incidental consequence of adding fleet metadata.
Fleet usage visibility rules must continue to hide all quota-derived fields.

## Goals / Non-Goals

**Goals:**

- Reuse usage rows already loaded by the accounts service.
- Expose one unambiguous quota-snapshot timestamp through the fleet response.
- Preserve the existing `/api/accounts` response and fleet privacy behavior.

**Non-Goals:**

- Renaming or changing the meaning of `lastRefreshAt`.
- Persisting a second freshness timestamp on the account row.
- Adding repository queries, database migrations, or frontend rendering.

## Decisions

1. **Compute freshness while building `AccountSummary`.** The account mapper is
   the last layer that has all standard usage-window rows available without a
   new query. It selects the newest non-null `recorded_at` after discarding a
   monthly sample that is not applicable to the account plan.

   Alternative considered: query `usage_history` from the fleet route. This
   would duplicate data access already performed by `list_accounts()` and add
   avoidable per-request work.

2. **Carry the timestamp through a non-serialized account-summary field.** The
   fleet mapper needs the value, but `/api/accounts` does not. A Pydantic field
   excluded from serialization keeps the typed projection explicit without
   expanding the accounts API contract.

   Alternative considered: add the field publicly to `AccountSummary`. This
   would create an unrelated API change and require broader client updates.

3. **Apply the existing fleet usage visibility gate to both timestamps.** When
   quota data is hidden, the fleet mapper emits `null` for `lastRefreshAt` and
   `usageRefreshedAt`, matching the existing treatment of quota windows.

## Risks / Trade-offs

- [Mixed naive and timezone-aware database timestamps] -> Compare normalized
  UTC values while returning the original value for the shared serializer.
- [A future usage window is added but omitted from freshness] -> Keep timestamp
  selection adjacent to standard-window normalization and cover all currently
  loaded standard windows in the mapper test.
- [Internal plumbing leaks through `/api/accounts`] -> Exclude the field at the
  Pydantic schema level and pin the external response with an integration test.

## Migration Plan

Deploy as an additive response-field change. Existing consumers continue using
`lastRefreshAt`; consumers that need quota freshness can adopt
`usageRefreshedAt`. Rollback removes the additive field and requires no data or
schema rollback.
