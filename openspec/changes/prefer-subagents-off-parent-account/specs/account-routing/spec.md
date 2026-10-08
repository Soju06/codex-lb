## ADDED Requirements

### Requirement: Fresh subagents can prefer an account other than their parent
The system MUST support a persisted subagent account preference with `off`, `parent_bound_only`, and `always` modes, defaulting to `off`. For a request positively identified as a subagent with an exact parent thread, the enabled preference MUST apply only when the child has no established exact-thread mapping and the request carries no hard ownership dependency. The system MUST first evaluate normal account eligibility while preferring an account other than the resolved parent owner and MUST retry normal selection with the parent allowed when no eligible alternative exists. A successful first selection MUST establish independent child affinity, and later child turns MUST follow that affinity rather than repeatedly alternate accounts.

The preference MUST NOT override `previous_response_id`, conversation, explicit turn-state, account-scoped file, live or durable bridge, source-pin, model, API-key scope, security, quota, health, concurrency, or single-account-routing constraints.

Parent response-binding evidence MUST be recorded only while the preference is enabled; with the preference `off`, routing MUST NOT write any subagent-lineage state. Recorded evidence MUST be one-way derived, MUST NOT appear in the dashboard sticky-session list or be treated as a hard `codex_session` mapping by stale-owner cleanup, and MUST expire after the prompt-cache affinity TTL.

#### Scenario: Disabled preference writes no lineage evidence
- **GIVEN** subagent account preference is `off`
- **WHEN** a request with an exact thread and `previous_response_id` resolves an account
- **THEN** no subagent-lineage evidence is persisted

#### Scenario: Lineage evidence is internal and expires
- **GIVEN** subagent account preference is enabled and parent response-binding evidence was recorded
- **WHEN** an operator lists sticky sessions or the stale hard-mapping cleanup runs
- **THEN** the evidence is neither listed nor tombstoned as a hard session
- **AND** periodic cleanup deletes it once it is older than the prompt-cache affinity TTL

#### Scenario: Parent-bound child uses another eligible account
- **GIVEN** `parent_bound_only` is enabled, a parent is positively bound by previous-response continuity to account A, and a fresh account-neutral child identifies that parent
- **WHEN** account B is otherwise eligible
- **THEN** the child initially binds to an eligible account other than account A
- **AND** subsequent child turns retain the child's independent binding

#### Scenario: Parent account is the safe fallback
- **GIVEN** subagent account preference is enabled for an account-neutral fresh child
- **WHEN** the parent account is the only account that satisfies normal routing constraints
- **THEN** the child is allowed to bind to the parent account

#### Scenario: Child hard continuity is never diversified
- **GIVEN** a subagent request carries a hard ownership dependency
- **WHEN** subagent account preference is enabled
- **THEN** the request remains bound to its required owner or fails closed under the existing continuity rules

#### Scenario: Existing child binding is stable
- **GIVEN** a child exact thread already has an account mapping
- **WHEN** a later account-neutral turn arrives with subagent account preference enabled
- **THEN** the preference does not displace or alternate that established child mapping

