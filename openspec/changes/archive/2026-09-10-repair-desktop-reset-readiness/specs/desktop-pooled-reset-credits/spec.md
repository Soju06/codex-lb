## MODIFIED Requirements

### Requirement: Inventory reflects genuine available credits

The enabled native reset list and optional usage reset summary MUST use the same complete fresh pool inventory. They MUST exclude paused, deactivated, reauthentication-required, pending-deletion and expired contributions. Missing, stale or inconsistent observations MUST NOT become invented credits. Credits MUST retain their genuine IDs, type and expiry, and duplicate IDs with different owners MUST be rejected as ambiguous. Refresh concurrency MUST be bounded and each concurrent operation MUST own its database session. Inventory refresh MUST release its database session before waiting for upstream credit or OAuth HTTP responses. Inventory failure MUST NOT replace otherwise valid pooled quota with fabricated quota or reset counts.

#### Scenario: Credits from two accounts are available

- **WHEN** eligible accounts report two and one available unexpired credits respectively
- **THEN** Desktop sees three genuine credits and their expiries

#### Scenario: Inventory is incomplete

- **WHEN** a required account snapshot is unavailable or stale after bounded refresh
- **THEN** the reset list reports unavailable and the optional usage reset summary is omitted


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
