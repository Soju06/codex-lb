# Model catalog compatibility delta

## ADDED Requirements

### Requirement: Complete account catalogs constrain pooled routing

The system MUST retain the union of successfully refreshed account model
catalogs for client discovery. When every active account has a current or
retained last-known catalog, request selection MUST route a model or explicit
service tier only to accounts whose own catalog advertised that capability.

#### Scenario: Same-plan accounts expose different models

- **GIVEN** two active accounts share a plan
- **AND** only one account advertises a model
- **WHEN** all active account catalogs are known
- **THEN** the merged discovery catalog includes the model
- **AND** requests for that model select only the advertising account

#### Scenario: Same-plan accounts expose different Fast tiers

- **GIVEN** two active accounts advertise the same model
- **AND** only one advertises the priority service tier
- **WHEN** a request explicitly asks for priority
- **THEN** selection considers only the account that advertised priority

### Requirement: Unknown account catalogs degrade without false exclusion

The system MUST distinguish an account catalog that successfully omitted a
capability from an account catalog that could not be fetched. If any active
account has neither a current nor retained last-known catalog, account-level
capability indexes MUST NOT be treated as authoritative and selection MUST use
the existing plan-level fallback. Operator-mapped model slugs MUST NOT be
rejected solely because they are absent from subscription catalog discovery.

When there is no authoritative account coverage — including partial refreshes
after prior successful cycles and when every account is removed and live
capability state is cleared — the static bootstrap catalog MUST remain the
discovery and plan-gating floor. Clearing capability state MUST NOT publish an
authoritative-empty catalog that reports canonical models as absent;
otherwise, in the window after an account is added but before the next
scheduled refresh, model/plan filtering would be skipped (an unsupported plan
could be selected) and `/v1/models` would report no models.

Carrying a plan's catalog forward when its refresh does not complete MUST NOT
re-advertise a model that no currently-active account of that plan advertises,
per the last-known per-account catalogs. This drop invariant MUST hold
regardless of whether the previous snapshot was authoritative: the authoritative
distinction governs whether per-account routing is trusted, not whether a dead
model is dropped from discovery. When a carried-forward model has no per-account
provenance at all (an older or plan-only snapshot that never captured per-account
catalogs), the system MUST preserve it rather than drop it, degrading safe when a
model cannot be attributed to any account.

#### Scenario: Catalog fetch partially fails after restart

- **GIVEN** there is no previous registry snapshot
- **AND** one active account catalog refresh succeeds while another fails
- **WHEN** selection evaluates a model or service tier
- **THEN** the partial index is non-authoritative
- **AND** the failed account is not classified as lacking every capability

#### Scenario: No active accounts fall back to the bootstrap floor

- **GIVEN** live capability state is cleared because no active accounts remain
- **WHEN** an account is added before the next scheduled refresh completes
- **THEN** canonical bootstrap models remain discoverable via `/v1/models`
- **AND** those models remain plan-gated by the bootstrap catalog
- **AND** an account whose plan does not support the model is not selected

#### Scenario: Failed refresh has last-known account data

- **GIVEN** every active account had a successful earlier catalog
- **AND** one account fails a later refresh
- **WHEN** that account remains active
- **THEN** its last-known capability data is retained
- **AND** the complete snapshot remains authoritative

#### Scenario: Account is paused or removed

- **GIVEN** an account has retained catalog capabilities
- **WHEN** it is no longer in the active account set
- **THEN** its capabilities no longer contribute to discovery or routing

#### Scenario: Removed account is the sole advertiser within a stale plan

- **GIVEN** two accounts share a plan and only one advertised a given model
- **AND** the plan's refresh does not complete this cycle, so its catalog is carried forward
- **AND** the sole advertiser is no longer in the active account set
- **AND** the other account of that plan remains active
- **WHEN** the stale plan's retained catalog is merged into discovery
- **THEN** the model advertised only by the removed account leaves discovery
- **AND** the models still advertised by the remaining active account are retained

#### Scenario: Sole advertiser removed under a non-authoritative previous snapshot

- **GIVEN** a first refresh recorded a model advertised by one account of a plan
- **AND** a same-plan account had no catalog, so the snapshot is non-authoritative
- **WHEN** that sole advertiser is removed while another same-plan account stays active
- **AND** the plan's refresh does not complete in a later cycle
- **THEN** the model advertised only by the removed account still leaves discovery

#### Scenario: Removed bootstrap model stays suppressed across repeated partial refreshes

- **GIVEN** a non-authoritative snapshot suppressed a bootstrap model because every last-known advertiser left the active account set
- **WHEN** later refresh cycles remain non-authoritative and still do not produce fresh active evidence for that model
- **THEN** the model stays absent from discovery and plan gating across those repeated partial refreshes

#### Scenario: Fresh active evidence clears bootstrap suppression

- **GIVEN** a bootstrap model was previously suppressed after its last-known advertisers left the active account set
- **WHEN** a later refresh records that an active account advertises that model again
- **THEN** the suppression is cleared
- **AND** the model returns to discovery and plan gating from live registry data

#### Scenario: Carried-forward model has unknown per-account provenance

- **GIVEN** a plan-only snapshot carried a model with no per-account provenance
- **WHEN** the plan is stale in a later refresh that knows the active account set
- **THEN** the model is preserved in discovery rather than dropped
