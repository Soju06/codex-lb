## ADDED Requirements

### Requirement: Gated model selection uses canonical additional-usage quota keys
When a request targets an explicitly gated model, the selection path MUST resolve that model through a canonical `model -> quota_key` mapping and use persisted `additional_usage_history` rows for that `quota_key` before normal candidate selection continues.

#### Scenario: Mapped model uses canonical quota key
- **WHEN** a request targets `gpt-5.3-codex-spark`
- **THEN** the selection path resolves the request through the mapped additional-usage `quota_key`
- **AND** candidate eligibility is evaluated from persisted rows for that canonical `quota_key`

### Requirement: Additional usage persistence normalizes upstream aliases to canonical quota keys
Persisted additional-usage rows MUST record one internal canonical `quota_key` even when upstream changes raw `limit_name` or `metered_feature` aliases.

#### Scenario: Upstream alias drift still lands on the same canonical key
- **WHEN** the usage API returns a known Spark quota alias such as `GPT-5.3-Codex-Spark` instead of `codex_other`
- **THEN** persistence stores the raw upstream fields for observability
- **AND** it records the same canonical `quota_key` used by routing for that model
- **AND** subsequent selection reads those rows through the canonical `quota_key`

### Requirement: Gated model selection fails closed on stale or missing quota data
Explicitly mapped gated models MUST NOT fall back to the general account pool when their persisted additional-usage snapshot is stale, missing, or yields zero eligible accounts.

#### Scenario: Stale additional-usage snapshot blocks mapped model routing
- **WHEN** a mapped model request resolves to a `quota_key` whose latest persisted snapshot is older than the freshness threshold
- **THEN** account selection returns no account
- **AND** the proxy reports a stable gated-model selection error instead of routing through unrelated accounts

#### Scenario: No eligible accounts for mapped quota key
- **WHEN** a mapped model request resolves to a fresh `quota_key` snapshot but every candidate account is ineligible for that quota
- **THEN** account selection returns no account
- **AND** the proxy reports a stable `no_additional_quota_eligible_accounts` style error instead of falling back to non-eligible accounts

### Requirement: Gated eligibility checks preserve balancer state semantics
Additional-quota eligibility filtering MUST NOT mutate persisted account status or change the meaning of `AccountState` fields while computing the candidate set.

#### Scenario: Eligibility pruning leaves persisted status unchanged
- **WHEN** gated-model selection evaluates candidate accounts against persisted additional-usage rows
- **THEN** it may read account status and runtime snapshots to decide eligibility
- **AND** it MUST NOT persist status changes or rewrite shared runtime state unless a normal post-selection balancer transition occurs
