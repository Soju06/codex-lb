## ADDED Requirements

### Requirement: Thread-scoped current Codex restarts still abandon a raw process-session owner

A self-contained Codex goal-continuation restart that also carries a distinct `thread-id` MUST still be eligible for the existing process-session abandonment exception. The request's thread-scoped locality source MUST NOT prevent the one-shot abandonment capability or the compare-and-set retirement of the raw process-session row.

The retirement write MUST remain scoped to `session_header`
interpretation of that raw key. An explicit `turn_state` lookup of the
same text MUST stay hard-bound to the stored account. After a
successful retirement, later same-thread turns that have no new hard
owner MUST keep continuity on the replacement account and MUST NOT
treat the `session_header`-abandoned raw row as live hard ownership.

Ordinary incremental, file-pinned, conversation-bound, and unresolved
tool-state requests MUST remain fail-closed on their required owner.

#### Scenario: Goal restart with process session and thread-id abandons the unavailable raw owner

- **GIVEN** a process-session identifier has a raw legacy `codex_session` mapping to account A
- **AND** account A is paused, rate-limited, or quota-exceeded
- **AND** account B is eligible
- **AND** the request also carries a distinct `thread-id`
- **WHEN** Codex sends the recognized goal-continuation marker with an account-neutral self-contained full resend and no other continuity dependency
- **THEN** the proxy marks the still-current raw mapping to account A abandoned only for process-session interpretation
- **AND** it routes the restarted turn to account B
- **AND** subsequent same-thread continuity remains on account B

#### Scenario: Thread-id on a goal restart cannot erase colliding explicit turn-state ownership

- **GIVEN** a raw legacy `codex_session` row was written as explicit turn-state ownership for account A
- **AND** a later request carries the same text as a process-session header plus a distinct `thread-id`
- **WHEN** a marked self-contained goal restart abandons that text for process-session interpretation
- **THEN** the restart may select account B
- **AND** an explicit turn-state lookup of the same text remains hard-bound to account A

#### Scenario: Account-dependent thread-scoped restart stays fail-closed

- **GIVEN** a process-session identifier has a raw legacy mapping to unavailable account A
- **AND** the request carries a distinct `thread-id`
- **AND** the body has a previous response, conversation, file pin, or unresolved tool state
- **WHEN** the request is selected
- **THEN** the request fails closed on account A
- **AND** the raw mapping is neither deleted nor rebound
