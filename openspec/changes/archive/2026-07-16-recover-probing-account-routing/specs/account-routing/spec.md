## ADDED Requirements

### Requirement: Probing accounts receive bounded recovery admission

For routing strategies that use the health-tier candidate pool, the load balancer MUST give replica-local `PROBING` accounts bounded opportunities to receive recovery traffic while healthy accounts remain. A probing account MUST become due when it has never been selected or at least the fixed probe quiet interval has elapsed since its last selection. When one or more probing accounts are due, selection MUST admit only the oldest-due probing account, using account id as a stable tie-break, ahead of the healthy pool for that selection. An unbound sticky selection or a sticky selection that can fall back from its existing owner MUST reserve that admission in replica-local runtime state before releasing the runtime lock for sticky repository work, so concurrent requests cannot consume the same due interval. The reservation MUST retain both its timestamp token and the runtime version captured at reservation. It MUST remain provisional through selection-time sticky repository work, the final local lease check, and selection-state persistence, and MUST be committed only if both captured values remain current when selection returns the probing account. If either value becomes stale, selection MUST release the reservation and retry without returning the stale probe. Sticky delete or upsert decisions made during selection MUST remain provisional through hard-sticky cap classification, final lease admission, selection-state persistence, and the probe reservation commit when applicable. Every other outcome MUST release the reservation without consuming the interval. Provisional reserve/release operations MUST NOT advance the runtime health-observation version used to reject stale Force Probe settlement; a committed recovery admission MUST advance it. When no probing account is due, healthy-first ordering MUST remain unchanged.

Recovery admission MUST occur only after all ordinary account eligibility, cooldown, model, security, quota, and local account-cap gates, but before budget and `burn_first`/`normal`/`preserve` preference shortcuts that could otherwise mask the due account. A hard-sticky owner MAY remain in the candidate set despite its local account cap solely to preserve fail-closed ownership, but every wider fallback candidate MUST pass the cap gate. When every otherwise available fallback is cap-filtered, an unavailable but under-cap fallback MUST NOT suppress the stable local account-cap error or be selected through transient-backoff fallback. Non-cap availability before and after cap filtering MUST be evaluated over each complete fallback pool so cross-account opportunistic eligibility is preserved, and an established local cap error MUST NOT be replaced by the opportunistic burn-window error. Returning a local cap error MUST preserve the existing hard-sticky owner mapping without deleting or rebinding it. Recovery admission MUST NOT displace a selectable existing sticky owner merely to probe another account, and it MUST NOT change the behavior of routing strategies that intentionally bypass health-tier pool ordering.

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

#### Scenario: Lease race releases a provisional probe admission

- **GIVEN** a sticky request reserves a due probing account while it performs sticky persistence
- **AND** another request fills that account's local concurrency cap before the final lease check
- **WHEN** the reserving request rejects the probing account and selects or reports another outcome
- **THEN** the probing account's prior selection timestamp is restored
- **AND** the quiet interval is not consumed by traffic that was never admitted

#### Scenario: Released reservation does not invalidate Force Probe settlement

- **GIVEN** an accepted Force Probe is loading usage for a probing account
- **AND** a concurrent sticky request reserves and then releases that probing account without selecting it
- **WHEN** the Force Probe settles against otherwise unchanged runtime health
- **THEN** its success is not rejected as stale because of the provisional reservation
- **AND** reserve/release does not advance the runtime health-observation version

#### Scenario: Newer health observation invalidates a sticky probe reservation

- **GIVEN** a sticky request reserves a due probing account and begins sticky repository work
- **AND** a newer runtime health observation advances that account's version and changes its health tier before admission commits
- **WHEN** the stale sticky selection attempts to return the reserved probing account
- **THEN** selection releases the reservation and retries against the newer runtime state
- **AND** it does not return the stale probe or persist sticky affinity to it

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
- **AND** it does not report global upstream unavailability or delete or rebind the sticky mapping

#### Scenario: Unavailable under-cap fallback does not mask cap pressure

- **GIVEN** a hard-sticky owner is temporarily unavailable
- **AND** every otherwise available wider fallback is at its local concurrency cap
- **AND** another wider fallback is under cap but unavailable because of quota, rate limit, cooldown, or account status
- **WHEN** sticky fallback selection cannot retain the owner
- **THEN** selection returns the stable local account-cap error
- **AND** the unavailable under-cap fallback does not cause global upstream-unavailability classification

#### Scenario: Opportunistic fallback cap pressure is classified over the complete pool

- **GIVEN** a hard-sticky owner is temporarily unavailable
- **AND** multiple wider fallbacks are opportunistically eligible when evaluated together
- **AND** every such fallback is at its local concurrency cap
- **WHEN** opportunistic sticky fallback selection cannot retain the owner
- **THEN** selection returns the stable local account-cap error
- **AND** it does not replace that reason with the opportunistic burn-window error

#### Scenario: Backoff-only fallback does not bypass local cap pressure

- **GIVEN** a hard-sticky owner is temporarily unavailable
- **AND** every normally usable wider fallback is at its local concurrency cap
- **AND** another under-cap fallback is still in transient error backoff
- **WHEN** sticky fallback selection evaluates the post-cap pool
- **THEN** it returns the stable local account-cap error
- **AND** it does not select or bind the backoff-only fallback

#### Scenario: Cap classification discards a provisional sticky deletion

- **GIVEN** an unavailable hard-sticky owner would otherwise be reallocated because of budget pressure
- **AND** every normally usable wider fallback is at its local concurrency cap
- **WHEN** selection finalizes the stable local account-cap error
- **THEN** any provisional delete or rebind decision is discarded
- **AND** the existing hard-sticky owner mapping remains unchanged
