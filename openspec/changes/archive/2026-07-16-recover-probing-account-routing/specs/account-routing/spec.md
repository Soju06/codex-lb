## ADDED Requirements

### Requirement: Probing accounts receive bounded recovery admission

For routing strategies that use the health-tier candidate pool, the load balancer MUST give replica-local `PROBING` accounts bounded opportunities to receive recovery traffic while healthy accounts remain. A probing account MUST become due when it has never been selected or at least the fixed probe quiet interval has elapsed since its last selection. When one or more probing accounts are due, selection MUST admit only the oldest-due probing account, using account id as a stable tie-break, ahead of the healthy pool for that selection. An unbound sticky selection or a sticky selection that can fall back from its existing owner MUST reserve that admission in replica-local runtime state before releasing the runtime lock for sticky persistence, so concurrent requests cannot consume the same due interval. The reservation MUST be released without consuming the interval when the request retains another account. When no probing account is due, healthy-first ordering MUST remain unchanged.

Recovery admission MUST occur only after all ordinary account eligibility, cooldown, model, security, quota, and local account-cap gates, but before budget and `burn_first`/`normal`/`preserve` preference shortcuts that could otherwise mask the due account. A hard-sticky owner MAY remain in the candidate set despite its local account cap solely to preserve fail-closed ownership, but every wider fallback candidate MUST pass the cap gate. Recovery admission MUST NOT displace a selectable existing sticky owner merely to probe another account, and it MUST NOT change the behavior of routing strategies that intentionally bypass health-tier pool ordering.

#### Scenario: Due probing account progresses while a healthy account exists

- **GIVEN** one eligible healthy account and one eligible probing account whose last selection is older than the fixed probe quiet interval
- **WHEN** an unbound health-tier-aware selection occurs
- **THEN** the probing account is selected for one recovery attempt
- **AND** its selection timestamp prevents another bounded recovery admission until the quiet interval elapses again

#### Scenario: Recent probing account does not displace healthy routing

- **GIVEN** one eligible healthy account and one eligible probing account selected less than the fixed probe quiet interval ago
- **WHEN** health-tier-aware selection occurs
- **THEN** the healthy account is selected

#### Scenario: Routing policy cannot starve a due probe

- **GIVEN** an eligible probing account is due while a healthy `burn_first` or non-preserved account remains
- **WHEN** health-tier-aware selection applies routing-policy preferences
- **THEN** the due probing account receives the bounded recovery admission before those preferences

#### Scenario: Oldest due probing account rotates fairly

- **GIVEN** multiple eligible probing accounts are due while healthy accounts remain
- **WHEN** health-tier-aware selection occurs
- **THEN** only the probing account with the oldest selection timestamp is admitted
- **AND** account id deterministically breaks an exact timestamp tie

#### Scenario: Existing sticky owner is retained

- **GIVEN** a request has a selectable sticky owner on a healthy account
- **AND** another account is due for probing recovery
- **WHEN** sticky selection occurs
- **THEN** the existing owner remains selected
- **AND** the sticky mapping is not rebound for recovery sampling

#### Scenario: Concurrent unbound stickies share one recovery admission

- **GIVEN** a probing account is due while a healthy account remains
- **AND** multiple requests concurrently observe distinct sticky keys with no existing owner
- **WHEN** those requests perform sticky selection
- **THEN** at most one request selects the due probing account for that quiet interval
- **AND** the other requests observe the reservation and retain healthy-first routing

#### Scenario: Concurrent sticky fallbacks share one recovery admission

- **GIVEN** a probing account is due while a healthy account remains
- **AND** multiple sticky requests have existing owners that are temporarily unavailable
- **WHEN** those requests concurrently select from the wider fallback pool
- **THEN** at most one request selects the due probing account for that quiet interval
- **AND** the other fallback requests observe the reservation and retain healthy-first routing

#### Scenario: Saturated probing account is excluded from sticky fallback

- **GIVEN** a hard-sticky owner is temporarily unavailable
- **AND** a due probing fallback account is at its local concurrency cap
- **AND** an eligible healthy fallback remains below its cap
- **WHEN** sticky fallback selection occurs
- **THEN** the saturated probing account is excluded from the fallback pool
- **AND** the healthy account is selected without rebinding the sticky mapping to the saturated account

#### Scenario: Saturated-only sticky fallback reports local cap pressure

- **GIVEN** a hard-sticky owner is temporarily unavailable
- **AND** every wider fallback account is at its local concurrency cap
- **WHEN** sticky fallback selection cannot retain the owner
- **THEN** selection returns the stable local account-cap error
- **AND** it does not report global upstream unavailability or rebind the sticky mapping
