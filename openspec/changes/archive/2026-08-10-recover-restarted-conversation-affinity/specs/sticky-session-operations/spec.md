## MODIFIED Requirements

### Requirement: Sticky sessions are explicitly typed
The system SHALL persist each sticky-session mapping with an explicit kind so durable Codex backend affinity, durable dashboard sticky-thread routing, and bounded prompt-cache affinity can be managed independently. Budget-pressure reallocation MUST apply only to mappings whose kind/source is soft. A raw or legacy `codex_session` mapping MUST remain owner-bound because it may represent explicit turn-state continuity; budget pressure MUST NOT delete or rebind it.

An explicit Codex goal-continuation restart MAY abandon a raw legacy `codex_session` owner only when the complete Responses payload is account-neutral and self-contained: it MUST have no nonblank `previous_response_id`, no nonblank `conversation`, no account-scoped input file or image reference, and no unresolved or orphan tool state. The owner MUST be persisted as `PAUSED`, `RATE_LIMITED`, or `QUOTA_EXCEEDED`; local capacity, retry exclusions, runtime health, and budget pressure MUST NOT authorize abandonment. The retirement write MUST compare the current mapping owner and unavailable account status atomically, MUST preserve a concurrently changed mapping or recovered owner, and on success MUST let normal selection establish affinity to the replacement account.

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
- **THEN** the proxy tombstones the still-current raw mapping to account A
- **AND** it routes the restarted turn to account B
- **AND** subsequent session or response continuity remains on account B

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
