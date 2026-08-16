## ADDED Requirements

### Requirement: Captured live snapshots survive account consolidation

The proxy MUST enqueue both the serving local account id and the upstream
ChatGPT account id when both are available. At consumption, live usage
ingestion MUST prefer the captured local id when it still identifies an
account. If that local id is absent or no longer exists, ingestion MUST use the
captured upstream id only when it resolves to exactly one current local account.
For a selected owner, ingestion MUST atomically persist no more than one history
row for each window represented by the queued snapshot. On PostgreSQL,
ingestion MUST acquire a transaction-scoped advisory lock keyed by the captured
upstream identity before either owner lookup and hold it through snapshot
commit. If the selected local owner's current non-null upstream identity is not
already locked, ingestion MUST roll back the initial transaction, reacquire the
captured and current identity locks in canonical sorted order, and reselect and
revalidate the owner before persistence. If that local owner was consolidated
while the current-identity lock was acquired, ingestion MUST use the last
observed current identity only when it resolves to exactly one surviving local
account. Ingestion MUST perform at most one such relock and MUST raise a typed
error if the selected owner's identity changes again; a null current identity
MUST NOT create an advisory-lock key. Every account writer that can change
membership in an upstream identity MUST acquire the same lock before row locks
or mutation and hold it through commit. Writers moving membership between two
non-null upstream identities MUST acquire both stable lock keys in canonical
sorted order.

#### Scenario: Stale duplicate settles under the unique canonical account

- **GIVEN** a primary/secondary live snapshot was queued for duplicate account `D`
- **AND** the queued item contains `D` and the upstream identity shared with canonical account `C`
- **AND** duplicate reconciliation reparents existing history to `C` and deletes `D`
- **WHEN** the queued snapshot is consumed
- **THEN** exactly one primary row and one secondary row are persisted under `C`
- **AND** no usage-history row is persisted under `D`
- **AND** the persisted values equal the captured snapshot

#### Scenario: A valid local owner takes precedence

- **GIVEN** a queued snapshot contains a local account id that still exists
- **AND** it also contains an upstream identity usable for fallback
- **WHEN** the queued snapshot is consumed
- **THEN** the snapshot is persisted under the captured local account
- **AND** ingestion does not substitute another account selected by the upstream identity

#### Scenario: A selected owner's current identity is revalidated

- **GIVEN** a queued snapshot contains local account `A` and captured identity `X`
- **AND** `A` currently belongs to identity `Y`
- **WHEN** settlement overlaps reconciliation of `A` into a canonical `Y` owner
- **THEN** settlement releases its initial `X` lock before acquiring the canonical sorted lock set for `X` and `Y`
- **AND** settlement reselects and revalidates the owner under that full lock set
- **AND** exactly one row per represented window survives under the canonical `Y` owner
- **AND** a second selected-owner identity change raises a typed terminal error without persisting the snapshot

#### Scenario: Upstream-only publication still resolves

- **GIVEN** a queued snapshot has no local account id
- **AND** its upstream identity resolves to exactly one current local account
- **WHEN** the queued snapshot is consumed
- **THEN** the snapshot is persisted once under that local account

#### Scenario: Consolidation cannot delete a snapshot inserted after reparenting

- **GIVEN** PostgreSQL settlement has selected duplicate `D` for a captured upstream identity
- **AND** reconciliation would reparent `D` history to `C` and then delete `D`
- **WHEN** settlement and reconciliation overlap across independent sessions
- **THEN** their shared transaction-scoped upstream-identity lock serializes the complete membership change
- **AND** the snapshot is either committed under `D` before reparenting or directly under `C` after reconciliation
- **AND** exactly one row per represented window survives under `C`

#### Scenario: Ambiguous fallback does not guess an owner

- **GIVEN** the captured local account id is absent or no longer exists
- **AND** the captured upstream identity matches multiple current local accounts
- **WHEN** the queued snapshot is consumed
- **THEN** no usage-history row is persisted for that snapshot
