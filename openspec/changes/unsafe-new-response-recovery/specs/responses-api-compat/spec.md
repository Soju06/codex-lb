## ADDED Requirements

### Requirement: Unsafe fresh-response recovery is explicitly bounded

When the unsafe new-response recovery flag is enabled together with
`server_indefinite_recovery`, the HTTP bridge MUST replace an upstream-rejected
`previous_response_id` with one fresh, anchor-free response. The request MUST
already have a verified, complete full-history replay body and a durable
one-shot recovery fence. Delta-only, incomplete, ambiguous, or already-replayed
requests MUST remain fail-closed. The flag MUST remain disabled by default.

#### Scenario: Verified full history receives one fresh response

- **GIVEN** an anchored request has a complete durable full-history body
- **AND** the upstream explicitly rejects its `previous_response_id`
- **AND** the unsafe recovery flag and `server_indefinite_recovery` are enabled
- **AND** the durable one-shot fence is claimed successfully
- **WHEN** the bridge retries the request
- **THEN** it sends the verified body without `previous_response_id`
- **AND** it binds the newly returned upstream response ID to the existing
  downstream session
- **AND** it does not permit a second unsafe fresh replay for that operation

#### Scenario: Delta-only or incomplete history fails closed

- **GIVEN** an anchored request has no verified complete full-history replay
- **WHEN** the upstream rejects its `previous_response_id`
- **THEN** the bridge does not strip the anchor and start a context-free turn
- **AND** it returns the existing continuity error contract

#### Scenario: Missing or consumed fence fails closed

- **GIVEN** the unsafe recovery flag is enabled
- **AND** the durable one-shot fence is unavailable or already consumed
- **WHEN** an anchored request encounters an explicit invalid-anchor error
- **THEN** the bridge does not dispatch a fresh response
- **AND** the operation remains eligible only for the normal fail-closed path

### Requirement: Unsafe recovery preserves downstream response identity

An unsafe fresh-response retry MUST preserve the downstream response identity and
event contract established before the replacement attempt. It MUST surface
terminal upstream failures through the existing sanitized Responses error
envelope and MUST NOT expose the replacement response ID as a second response
lifecycle to the caller.

#### Scenario: Replacement completion keeps the original downstream identity

- **GIVEN** the replacement upstream emits a successful terminal response
- **WHEN** the bridge settles the unsafe recovery operation
- **THEN** the downstream stream contains one response lifecycle
- **AND** the durable session anchor is updated to the replacement response ID

#### Scenario: Replacement failure remains a continuity error

- **WHEN** the fresh replacement attempt fails before producing a usable
  response
- **THEN** the bridge returns the stable continuity failure envelope
- **AND** it does not silently retry the same fenced operation again
