## MODIFIED Requirements

### Requirement: Sticky sessions are explicitly typed
The system SHALL persist each sticky-session mapping with an explicit kind so durable Codex backend affinity, durable dashboard sticky-thread routing, and bounded prompt-cache affinity can be managed independently. Budget-pressure reallocation MUST apply only to mappings whose kind/source is soft. A raw or legacy `codex_session` mapping MUST remain owner-bound because it may represent explicit turn-state continuity; budget pressure MUST NOT delete or rebind it.

An explicit Codex goal-continuation restart MAY abandon a raw legacy `codex_session` owner only when the complete Responses payload is account-neutral and self-contained: it MUST have no nonblank `previous_response_id`, no nonblank `conversation`, no account-scoped input file or image reference, and no unresolved or orphan tool state. Classification MUST use the canonical upstream request form so accepted compatibility controls and transport-envelope fields do not make equivalent requests disagree. The owner MUST be persisted as `PAUSED`, `RATE_LIMITED`, or `QUOTA_EXCEEDED` and MUST belong to the authenticated request's account-assignment and security-policy scope computed before model and service-tier eligibility; local capacity, model eligibility, retry exclusions, runtime health, budget pressure, and an out-of-scope owner MUST NOT determine mutation authority. The retirement write MUST compare the current mapping owner and unavailable account status atomically, MUST preserve a concurrently changed mapping or recovered owner, and on success MUST let normal selection establish affinity to the replacement account. Because a raw key's persisted source is ambiguous, goal-restart abandonment MUST apply only to `session_header` interpretation and MUST retain the stored account as hard ownership for an explicit `turn_state` lookup using the same text. During a rolling deployment or rollback, replicas that do not understand source-qualified abandonment MUST continue treating that retained account as hard ownership. A selector that observes source-qualified abandonment initially or after losing the retirement compare-and-set MUST exclude the retained retired owner until replacement affinity is persisted, even if its account inputs predate retirement.

#### Scenario: Soft sticky reallocation uses split primary and secondary pressure thresholds
- **WHEN** a request resolves an existing prompt-cache, sticky-thread, or other explicitly soft mapping
- **AND** the pinned account is otherwise eligible to serve traffic
- **AND** the pinned account is strictly above either the configured primary sticky reallocation threshold or the configured secondary sticky reallocation threshold
- **AND** another eligible account remains at or below both configured sticky reallocation thresholds
- **THEN** selection rebinds the sticky-session mapping to the healthier account before sending the request upstream

#### Scenario: Sticky reallocation preserves a pinned account when every candidate is split-threshold pressured
- **WHEN** a request resolves an existing soft sticky-session mapping
- **AND** the pinned account is otherwise eligible to serve traffic
- **AND** the pinned account is strictly above either configured sticky reallocation threshold
- **AND** every other eligible account is also strictly above at least one configured sticky reallocation threshold
- **THEN** selection retains the existing pinned account to avoid sticky-pin thrashing

#### Scenario: Fresh selection does not apply sticky secondary pressure threshold
- **WHEN** a request has no sticky-session mapping
- **AND** one eligible account is above the configured secondary sticky reallocation threshold but below the normal primary budget threshold
- **THEN** the account remains eligible for ordinary non-sticky routing according to the selected routing strategy

#### Scenario: Hard Codex mapping ignores budget-pressure reallocation

- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A is above a sticky budget-pressure threshold
- **AND** account B has more remaining budget
- **WHEN** the request is selected
- **THEN** selection remains constrained to account A
- **AND** the raw mapping is neither deleted nor rebound to account B

#### Scenario: Unavailable hard Codex owner does not lose its mapping

- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A is temporarily quota-exceeded or otherwise unusable
- **AND** account B is healthy
- **WHEN** an ordinary request or an unsafe restart-shaped request requires the mapping
- **THEN** the request fails closed instead of selecting account B
- **AND** the raw mapping is neither deleted nor rebound

#### Scenario: Self-contained goal restart abandons unavailable legacy owner

- **GIVEN** a process-session identifier has a raw legacy `codex_session` mapping to account A
- **AND** account A is paused, rate-limited, or quota-exceeded
- **AND** account B is eligible
- **WHEN** Codex sends the recognized goal-continuation marker with an account-neutral self-contained full resend and no other continuity dependency
- **THEN** the proxy marks the still-current raw mapping to account A abandoned only for process-session interpretation
- **AND** it routes the restarted turn to account B
- **AND** subsequent session or response continuity remains on account B

#### Scenario: Goal restart cannot erase colliding explicit turn-state ownership

- **GIVEN** a raw legacy `codex_session` row was written as explicit turn-state ownership for account A
- **AND** a process-session header later uses the same client-controlled text
- **WHEN** a marked self-contained goal restart abandons that text for process-session interpretation
- **THEN** the process-session restart may select account B
- **AND** an explicit turn-state lookup of the same text remains hard-bound to account A

#### Scenario: Source-qualified retirement fails closed on an older replica

- **GIVEN** a current replica marks a raw account A mapping abandoned only for `session_header` interpretation
- **WHEN** a replica that does not understand abandonment scope reads the same raw mapping
- **THEN** it continues to resolve account A as hard ownership
- **AND** it cannot re-pin a colliding explicit turn state to another account

#### Scenario: Model eligibility does not narrow retirement authority

- **GIVEN** unavailable account A is inside the authenticated account-assignment and security-policy scope
- **AND** account A cannot serve the restart's requested model while account B can
- **WHEN** a marked self-contained goal restart evaluates the raw mapping owned by account A
- **THEN** account A remains authorized for the guarded abandonment mutation
- **AND** model and service-tier eligibility apply only when selecting the replacement

#### Scenario: Equivalent request forms receive the same restart classification

- **GIVEN** two marked self-contained goal restarts differ only by accepted compatibility controls or a transport-only response-create envelope
- **WHEN** the proxy classifies their account-neutral replay safety
- **THEN** it evaluates the same canonical upstream request fields for both forms
- **AND** neither form remains pinned merely because its accepted input representation differs

#### Scenario: Stale selection snapshot cannot repin a retired owner

- **GIVEN** restart selection loaded account A as active before guarded retirement observes its unavailable persisted status
- **WHEN** guarded retirement tombstones account A's still-current raw legacy mapping
- **THEN** the remainder of that selection excludes account A from the stale snapshot
- **AND** the namespaced process-session mapping is not established on account A

#### Scenario: Retirement CAS loser excludes the winner's retired owner

- **GIVEN** two marked restarts read the same raw account A mapping and stale account inputs
- **AND** the first restart marks account A abandoned only for `session_header` interpretation
- **WHEN** the second restart loses its retirement compare-and-set and rereads that marker
- **THEN** the second restart excludes retained account A from its stale inputs
- **AND** it cannot establish replacement affinity on account A

#### Scenario: Goal marker does not override account-scoped continuity

- **GIVEN** a marked goal-continuation request carries a nonblank `previous_response_id`, nonblank `conversation`, account-scoped file or image reference, or unresolved tool output
- **WHEN** its hard owner is unavailable
- **THEN** the request fails closed
- **AND** the hard mapping is not abandoned

#### Scenario: Healthy owner is not abandoned

- **GIVEN** a marked account-neutral goal-continuation restart has a raw legacy owner that is still active
- **WHEN** the owner is locally capped, excluded, budget-pressured, or transiently unhealthy
- **THEN** the mapping remains owner-bound
- **AND** the restart does not retire it as unavailable

#### Scenario: Concurrent owner change wins retirement race

- **GIVEN** restart selection observed a raw legacy mapping to unavailable account A
- **WHEN** another operation rebinds that mapping or restores the owner before the retirement write executes
- **THEN** the compare-and-set retirement does not tombstone the newer state
- **AND** selection preserves fail-closed ownership semantics

#### Scenario: Scoped API key cannot retire another pool's owner

- **GIVEN** a raw legacy `codex_session` mapping points to unavailable account A
- **AND** the authenticated API key's effective account-policy scope contains account B but not account A
- **WHEN** the key sends a marked account-neutral goal-continuation restart for that session
- **THEN** the request fails closed before upstream dispatch
- **AND** the raw mapping to account A is neither tombstoned nor rebound

#### Scenario: Generated WebSocket turn state does not follow a restarted goal

- **GIVEN** the proxy generated the downstream turn-state header used by an upstream WebSocket on account A
- **AND** a marked self-contained goal restart retires that socket and selects account B
- **WHEN** the proxy opens the replacement WebSocket
- **THEN** it removes account A's generated turn-state token before connect
- **AND** it still forwards the restart's full replay payload to account B
