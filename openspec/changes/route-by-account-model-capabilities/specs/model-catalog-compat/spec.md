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

#### Scenario: Catalog fetch partially fails after restart

- **GIVEN** there is no previous registry snapshot
- **AND** one active account catalog refresh succeeds while another fails
- **WHEN** selection evaluates a model or service tier
- **THEN** the partial index is non-authoritative
- **AND** the failed account is not classified as lacking every capability

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

