# routing-pools Specification

## Purpose

Define the routable capacity pools the balancer exposes to external routers:
one aggregated view of how much of each vendor budget is actually spendable
right now, including the Anthropic Fable-scoped window that until now was only
readable as a boolean.

## Requirements

### Requirement: Accounts expose the Fable-scoped weekly window

`GET /api/accounts` SHALL include a `fableScopedWeekly` object on every
Anthropic account for which a Fable-scoped weekly marker row exists, carrying
`usedPercent`, `resetAt`, `recordedAt`, and `fresh`. `fresh` SHALL be true only
when the marker was recorded inside the same staleness window the routing path
uses for Fable eligibility. The field SHALL be null for accounts of other
providers and for Anthropic accounts with no marker row.

#### Scenario: A fresh marker is reported with its reset

- **GIVEN** an Anthropic account with a Fable-scoped marker recorded minutes ago
- **WHEN** the dashboard lists accounts
- **THEN** the account carries `fableScopedWeekly` with the marker's used
  percent and reset
- **AND** `fresh` is true

#### Scenario: A stale marker is still reported but not fresh

- **GIVEN** an Anthropic account whose Fable-scoped marker is older than the
  staleness window
- **WHEN** the dashboard lists accounts
- **THEN** `fableScopedWeekly.fresh` is false
- **AND** `fableEligible` is decided by the overall-weekly heuristic instead

#### Scenario: No marker means no window

- **GIVEN** an account with no Fable-scoped marker row
- **WHEN** the dashboard lists accounts
- **THEN** `fableScopedWeekly` is null

### Requirement: Pools endpoint reports routable capacity

The service SHALL expose `GET /api/pools` under dashboard-session
authentication, returning `generatedAt` and a `pools` array holding the
`anthropic-fable`, `anthropic-general`, `openai-codex`, `kimi`, and `glm`
pools. Each pool SHALL report `provider`, `kind`, `accounts`,
`eligibleAccounts`, `headroomPercent`, `aggregateRemainingPercent`, `resetAt`,
and `status`. `headroomPercent` SHALL be the best single usable account's
remaining percent and `aggregateRemainingPercent` the mean across usable
accounts. The response SHALL contain no account identities and no secrets.

#### Scenario: Every pool is reported even when empty

- **GIVEN** an installation with no accounts of a given provider
- **WHEN** a dashboard session requests the pools
- **THEN** that pool is still present with `accounts` zero and `status`
  `exhausted`

#### Scenario: Unauthenticated callers are rejected

- **GIVEN** a request without a valid dashboard session
- **WHEN** it requests `/api/pools`
- **THEN** the request is rejected by the same dashboard-session gate that
  guards `/api/sessions`

### Requirement: Pool status reflects spendable headroom

Pool `status` SHALL be `ok` when `headroomPercent` is at least 25, `low` when
it is at least 5 and below 25, and `exhausted` when it is below 5 or when the
pool has no usable account. Accounts whose subscription is canceled, and
accounts that are paused, deactivated, or awaiting re-auth, SHALL be excluded
from the pool arithmetic while still counting toward `accounts`.

#### Scenario: A nearly spent pool reads low

- **GIVEN** the best usable account in a pool has 13 percent of its window left
- **WHEN** a dashboard session requests the pools
- **THEN** that pool's `status` is `low`

#### Scenario: A pool of held accounts is exhausted

- **GIVEN** every account of a provider is canceled, paused, deactivated, or
  awaiting re-auth
- **WHEN** a dashboard session requests the pools
- **THEN** that pool reports its account count, `eligibleAccounts` zero, null
  headroom, and `status` `exhausted`

### Requirement: Fable pool names the signal it used

The `anthropic-fable` pool SHALL report `source` as `scoped_marker` when at
least one usable Anthropic account carries a fresh Fable-scoped marker, and
`weekly_heuristic` otherwise. Per account, the pool SHALL read the fresh
Fable-scoped marker when one exists and the overall-weekly window otherwise.
`eligibleAccounts` for this pool SHALL be the number of usable accounts the
balancer currently considers Fable-eligible.

#### Scenario: Fresh markers make the pool authoritative

- **GIVEN** at least one usable Anthropic account has a fresh Fable-scoped
  marker
- **WHEN** a dashboard session requests the pools
- **THEN** the Fable pool's `source` is `scoped_marker`
- **AND** accounts without a fresh marker still contribute their overall-weekly
  remaining

#### Scenario: Only stale markers fall back to the heuristic

- **GIVEN** every Fable-scoped marker is older than the staleness window
- **WHEN** a dashboard session requests the pools
- **THEN** the Fable pool's `source` is `weekly_heuristic`
- **AND** its headroom comes from the overall-weekly windows

### Requirement: Fable-scoped routing reserves the end of the week

The Fable-scoped eligibility threshold SHALL default to 90 percent used, so
that routed Fable traffic stops before the vendor window is fully spent and
the remaining budget stays available for interactive work.

#### Scenario: An account past the reserve is not Fable-eligible

- **GIVEN** default settings and an Anthropic account with a fresh Fable-scoped
  marker at 95 percent used
- **WHEN** the dashboard lists accounts
- **THEN** that account reports `fableEligible` false
