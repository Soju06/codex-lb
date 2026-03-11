## ADDED Requirements

### Requirement: Gated model selection uses canonical additional-usage limit names
When a request targets an explicitly gated model, the selection path MUST resolve that model through a canonical `model -> limit_name` mapping and use persisted `additional_usage_history` rows for that `limit_name` before normal candidate selection continues.

#### Scenario: Mapped model uses canonical limit name
- **WHEN** a request targets `gpt-5.3-codex-spark`
- **THEN** the selection path resolves the request through the mapped additional-usage `limit_name`
- **AND** candidate eligibility is evaluated from persisted rows for that canonical `limit_name`

### Requirement: Gated model selection fails closed on stale or missing quota data
Explicitly mapped gated models MUST NOT fall back to the general account pool when their persisted additional-usage snapshot is stale, missing, or yields zero eligible accounts.

#### Scenario: Stale additional-usage snapshot blocks mapped model routing
- **WHEN** a mapped model request resolves to a `limit_name` whose latest persisted snapshot is older than the freshness threshold
- **THEN** account selection returns no account
- **AND** the proxy reports a stable gated-model selection error instead of routing through unrelated accounts

#### Scenario: No eligible accounts for mapped limit name
- **WHEN** a mapped model request resolves to a fresh `limit_name` snapshot but every candidate account is ineligible for that limit
- **THEN** account selection returns no account
- **AND** the proxy reports a stable `no_additional_quota_eligible_accounts` style error instead of falling back to non-eligible accounts

### Requirement: Gated eligibility checks preserve balancer state semantics
Additional-quota eligibility filtering MUST NOT mutate persisted account status or change the meaning of `AccountState` fields while computing the candidate set.

#### Scenario: Eligibility pruning leaves persisted status unchanged
- **WHEN** gated-model selection evaluates candidate accounts against persisted additional-usage rows
- **THEN** it may read account status and runtime snapshots to decide eligibility
- **AND** it MUST NOT persist status changes or rewrite shared runtime state unless a normal post-selection balancer transition occurs
