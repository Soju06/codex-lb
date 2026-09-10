# Desktop pooled reset credits specification

## Purpose

Expose genuine imported reset credits in Codex Desktop and redeem one credit on its owner with stable retry ownership.

## Requirements

### Requirement: Operator controls pool redemption

The system MUST default Desktop reset pooling to disabled and expose its policy through dashboard settings. Enabling the policy MUST require dashboard write access. A native reset request MUST authenticate as an eligible imported ChatGPT account; an API key alone MUST NOT authorize pool redemption. Disabling pooling MUST preserve original-account credit inventory and consumption. Billing, subscriptions and non-reset account fields MUST remain account-owned.

#### Scenario: Existing setup leaves credits account-owned

- **WHEN** pooling is disabled
- **THEN** native reset inventory and consumption remain limited to the signed-in account

#### Scenario: Pool redemption is explicitly enabled

- **WHEN** a dashboard writer enables pooling and an eligible imported ChatGPT identity requests a reset
- **THEN** the request may select from the eligible imported pool

### Requirement: Inventory reflects genuine available credits

The enabled native reset list and optional usage reset summary MUST use the same complete fresh pool inventory. They MUST exclude paused, deactivated, reauthentication-required, pending-deletion and expired contributions. Missing, stale or inconsistent observations MUST NOT become invented credits. Credits MUST retain their genuine IDs, type and expiry, and duplicate IDs with different owners MUST be rejected as ambiguous. Refresh concurrency MUST be bounded and each concurrent operation MUST own its database session. Inventory refresh MUST release its database session before waiting for upstream credit or OAuth HTTP responses. Inventory failure MUST NOT replace otherwise valid pooled quota with fabricated quota or reset counts.

#### Scenario: Credits from two accounts are available

- **WHEN** eligible accounts report two and one available unexpired credits respectively
- **THEN** Desktop sees three genuine credits and their expiries

#### Scenario: Inventory is incomplete

- **WHEN** a required account snapshot is unavailable or stale after bounded refresh
- **THEN** the reset list reports unavailable and the optional usage reset summary is omitted

### Requirement: Default redemption uses earliest expiry

A default reset MUST select the available credit with the earliest known expiry from fresh eligible inventory. Credits without an expiry MUST sort after known expiries. Ties MUST use deterministic owner and credit ordering. An explicit credit selection MUST be honored only within the authorized eligible inventory. One action MUST consume at most one credit on its owning account and refresh the affected usage and credit observations.

#### Scenario: Earlier expiry belongs to another account

- **WHEN** the signed-in account's credit expires next week and another eligible account's credit expires tomorrow
- **THEN** the default action selects tomorrow's credit using its owner's credentials

#### Scenario: User selects a particular credit

- **WHEN** the user explicitly selects a currently available credit
- **THEN** the system redeems that credit on its owner rather than substituting another

### Requirement: Retries preserve the selected owner and credit

Before upstream consumption, the system MUST durably bind caller identity and redemption request ID to one local owner, stable upstream owner identity and credit. Concurrent first attempts MUST converge on the winning durable binding. Every retry MUST reuse that binding and MUST NOT select another credit after timeout, transport failure, ownership changes or deletion. Existing per-owner serialization and upstream idempotency MUST remain effective across replicas. Rejected and uncertain outcomes MUST remain truthful; they MUST NOT be reported as a reset. Operator policy and owner eligibility MUST be rechecked before a new upstream consume.

#### Scenario: Consume succeeded but the response was lost

- **WHEN** the same request is retried on another replica
- **THEN** it targets the same owner and credit with the same upstream idempotency key

#### Scenario: Pinned owner becomes unavailable

- **WHEN** a retry's pinned owner has been deleted or made ineligible
- **THEN** the request fails without spending a different account's credit

#### Scenario: Backend rejects the reset

- **WHEN** upstream reports no credit, nothing to reset or an uncertain transport result
- **THEN** the native response preserves that outcome without switching accounts or claiming success

#### Scenario: Redemption ledgers disagree

- **WHEN** the helper ledger and permanent Desktop binding disagree for the same request
- **THEN** the endpoint returns HTTP 409 with `reset_credit_request_conflict` without consuming a credit


### Requirement: Reset pooling shares one migration head with main

The database MUST upgrade to one Alembic head from either the reset-pooling revision or the main revision. Upgrading MUST preserve existing settings and redemption bindings. Downgrading only the merge MUST preserve both parent schemas and their data.

#### Scenario: Existing reset binding survives integration

- **WHEN** a database at the reset-pooling revision contains a redemption binding and upgrades to head
- **THEN** the original owner and credit binding remain unchanged
- **AND** the resulting schema matches the application models

#### Scenario: Main database enables the new schema

- **WHEN** a database at the main revision upgrades to head
- **THEN** reset pooling defaults to disabled and existing settings remain unchanged

#### Scenario: Operator reverses only the merge

- **WHEN** the merge revision downgrades to either immediate parent
- **THEN** both parent stamps and all settings and bindings remain intact


### Requirement: Reset and spool-retention migration composition preserves data

The database MUST upgrade to one head from either the Desktop reset merge revision or dashboard spool-retention revision. Existing reset owner/credit bindings, reset policy and explicit spool-retention settings MUST survive upgrade. Downgrading only the composition merge MUST preserve both parent schemas and their data.

#### Scenario: Existing reset database adopts spool retention

- **WHEN** an existing reset database upgrades to the composition head
- **THEN** its reset policy and exact redemption bindings remain unchanged
- **AND** the new retention column is nullable and inherits the existing policy

#### Scenario: Existing retention value survives merge downgrade

- **WHEN** a database with an explicit retention value upgrades and downgrades only the composition merge
- **THEN** the value and any redemption bindings remain unchanged
- **AND** both parent revisions remain stamped


### Requirement: Reset and guest-generation composition preserves authentication state

The database MUST upgrade to one head from either the published reset/spool merge or guest-generation revision. Upgrade and merge-only downgrade MUST preserve existing reset bindings, reset policy, admin and guest credentials, guest-access state, retention settings and nonzero guest generations. A database without the generation column MUST receive the existing zero default through the guest parent migration. Existing guest revocation and protected-search permissions MUST remain unchanged.

#### Scenario: Reset database adopts guest generation

- **WHEN** a populated reset/spool database upgrades to the composition head
- **THEN** exact reset owner and credit bindings and stored settings remain unchanged
- **AND** guest generation is initialized to zero

#### Scenario: Guest generation survives composition rollback

- **WHEN** a database with a nonzero guest generation upgrades and downgrades only the composition merge
- **THEN** the stored generation and credentials remain unchanged
- **AND** both direct parent revisions remain stamped

### Requirement: Reset pooling composes with dashboard users without state loss

The database MUST upgrade to one head from the published reset/guest merge and dashboard audit-attribution revision. Existing audit rows, roles, grants, users, identities, credentials, user and guest generations, reset bindings, reset policy and explicit retention settings MUST survive. Missing user schema MUST receive main's existing credential backfill and preset roles. Merge-only downgrade and re-upgrade MUST retain both parent schemas and their data. Existing authorization and CSRF rules MUST remain unchanged.

#### Scenario: Populated reset database adopts dashboard users

- **WHEN** a reset database with credentials and bound credits upgrades to the merge
- **THEN** main's role and user migrations run with their original credential semantics
- **AND** exact reset bindings and existing settings remain unchanged

#### Scenario: Populated user database adopts reset pooling

- **WHEN** a database with custom grants, identities and nonzero session generations upgrades
- **THEN** all existing rows remain unchanged and pooling defaults off
- **AND** merge-only downgrade retains both parent stamps and data

### Requirement: Reset pooling composes with dashboard invitations

The database MUST upgrade to one head from either the published reset/user merge or dashboard invitation revision. Existing invitation hashes, lifecycle timestamps, flags and inviter snapshots MUST remain unchanged, together with users, roles, grants, session generations, audit data, reset policy and redemption bindings. Merge-only downgrade and re-upgrade MUST preserve both parent schemas and their data. Existing user-management, authorization and CSRF behavior MUST remain unchanged.

#### Scenario: Populated invitation data survives composition

- **WHEN** a database with pending, consumed or revoked invitation rows upgrades and rolls back only the merge
- **THEN** every invitation column and its associated user state remains unchanged
- **AND** both direct parent revisions remain stamped

#### Scenario: Reset data adopts invitations

- **WHEN** a reset database upgrades to the composition head
- **THEN** the invitation table is created empty without changing reset bindings or authorization
