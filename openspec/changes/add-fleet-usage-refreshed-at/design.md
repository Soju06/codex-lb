## Context

`GET /api/fleet/summary` currently projects `AccountSummary.last_refresh_at`,
which originates from `Account.last_refresh` and therefore describes OAuth
credential refresh. `AccountsService.list_accounts()` already loads the latest
primary, secondary, and monthly usage rows used to build each account summary,
but the account response intentionally does not expose their persistence time.

The fleet contract needs the newest of those existing `recorded_at` values
without adding a query or changing the broader dashboard accounts response.

## Goals / Non-Goals

**Goals:**

- Add an explicit nullable usage-snapshot freshness timestamp to fleet summary
  accounts.
- Derive it from already loaded standard usage rows.
- Preserve the existing usage-visibility boundary and `lastRefreshAt`
  semantics.
- Prove that successful operator-triggered usage writes advance usage
  freshness independently of OAuth refresh.

**Non-Goals:**

- Rename or remove `lastRefreshAt`.
- Add storage, migrations, queries, refresh scheduling, or new refresh paths.
- Change OAuth, probe, or usage-refresh behavior.
- Change the dashboard accounts API or dashboard rendering.
- Include additional-quota samples or refactor usage loading.

## Decisions

### Carry usage freshness as non-serialized account-summary metadata

The account mapper will calculate the newest `recorded_at` across the primary,
secondary, and monthly rows passed to it. It will store that value in a Pydantic
private attribute on `AccountSummary` and expose a read-only accessor for the
fleet mapper. Private metadata follows the existing `AccountProbeResponse`
pattern and prevents the internal bridge from becoming a new
`GET /api/accounts` response field.

The fleet response schema alone gains the public `usageRefreshedAt` field.

Alternatives considered:

- Query usage history again in the fleet route. Rejected because the needed
  rows are already loaded and the accepted contract forbids another query.
- Add a serialized field to `AccountSummary`. Rejected because that would
  broaden the dashboard accounts API beyond the accepted fleet-only scope.
- Reimplement account and usage loading in the fleet module. Rejected because
  it would duplicate account mapping and usage-window semantics.

### Reuse the existing fleet usage-visibility switch

The fleet mapper will emit the internal timestamp only when `include_usage` is
true. When the API key or global privacy setting hides usage, the field will be
`null`, matching the existing treatment of quota windows and `lastRefreshAt`.

### Treat refresh advancement as a consequence of a successful usage write

Force Probe and `POST /api/fleet/refresh` already persist standard usage
snapshots through the shared updater. No refresh orchestration changes are
needed. Regression tests will observe a later fleet summary after each path
writes a newer snapshot and will prove `lastRefreshAt` stays unchanged when the
OAuth token was not refreshed.

## Risks / Trade-offs

- **Private metadata is lost if a summary is reconstructed elsewhere** → Only
  the existing account mapper creates fleet inputs, and focused tests cover
  both serialization boundaries.
- **Naive SQLite timestamps and aware timestamps are mixed** → Compare only
  database-loaded `UsageHistory.recorded_at` values from the same session
  conventions; dashboard serialization already renders naive values as UTC.
- **A refresh legitimately skips or writes no usage** → The timestamp remains
  unchanged, preserving the existing refresh policy rather than manufacturing
  freshness.
