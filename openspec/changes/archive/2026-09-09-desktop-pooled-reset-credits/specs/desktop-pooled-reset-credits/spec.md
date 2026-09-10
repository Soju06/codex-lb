## ADDED Requirements

### Requirement: Operator controls pool redemption

The system MUST default Desktop reset pooling to disabled and expose its policy through dashboard settings. Enabling the policy MUST require dashboard write access. A native reset request MUST authenticate as an eligible imported ChatGPT account; an API key alone MUST NOT authorize pool redemption. Disabling pooling MUST preserve original-account credit inventory and consumption. Billing, subscriptions and non-reset account fields MUST remain account-owned.

#### Scenario: Existing setup leaves credits account-owned

- **WHEN** pooling is disabled
- **THEN** native reset inventory and consumption remain limited to the signed-in account

#### Scenario: Pool redemption is explicitly enabled

- **WHEN** a dashboard writer enables pooling and an eligible imported ChatGPT identity requests a reset
- **THEN** the request may select from the eligible imported pool

### Requirement: Inventory reflects genuine available credits

The enabled native reset list and optional usage reset summary MUST use the same complete fresh pool inventory. They MUST exclude paused, deactivated, reauthentication-required, pending-deletion and expired contributions. Missing, stale or inconsistent observations MUST NOT become invented credits. Credits MUST retain their genuine IDs, type and expiry, and duplicate IDs with different owners MUST be rejected as ambiguous. Refresh concurrency MUST be bounded and each concurrent operation MUST own its database session. Inventory failure MUST NOT replace otherwise valid pooled quota with fabricated quota or reset counts.

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
