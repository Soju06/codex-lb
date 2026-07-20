# sticky-session-operations Delta Specification

## MODIFIED Requirements

### Requirement: Sticky sessions are explicitly typed

The system SHALL persist each sticky-session mapping with an explicit kind so durable Codex backend affinity, durable dashboard sticky-thread routing, and bounded prompt-cache affinity can be managed independently. Budget-pressure reallocation MUST apply only to mappings whose kind/source is soft. A raw or legacy `codex_session` mapping MUST remain owner-bound because it may represent explicit turn-state continuity; budget pressure MUST NOT delete or rebind it. Request-time selection MUST NOT reallocate a hard `codex_session` mapping to a different account under any circumstance, including when its owner is unavailable. Independently of request-time selection, a periodic background job MAY delete (never rebind) a hard `codex_session` mapping once its owner has been durably unavailable — not merely transiently rate-limited or paused — for well past its own recovery point, so a future request against that session simply re-resolves fresh instead of failing closed forever.

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

#### Scenario: Unavailable hard Codex owner does not lose its mapping at request time

- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A is temporarily quota-exceeded or otherwise unusable
- **AND** account B is healthy
- **WHEN** hard-owner selection fails
- **THEN** the request fails closed instead of selecting account B
- **AND** the raw mapping is neither deleted nor rebound by that request

#### Scenario: A durably unavailable hard Codex owner's mapping is eventually purged

- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A has been `PAUSED` since before a conservative cutoff, or `RATE_LIMITED`/`QUOTA_EXCEEDED` with its reset time before that same cutoff
- **WHEN** the periodic sticky-session cleanup job runs
- **THEN** the mapping is deleted
- **AND** it is not rebound to any other account
- **AND** the next request against that session resolves a fresh mapping instead of failing closed

#### Scenario: A merely transient hard Codex owner outage is never purged

- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A became rate-limited or paused more recently than the conservative cutoff
- **WHEN** the periodic sticky-session cleanup job runs
- **THEN** the mapping is left untouched
