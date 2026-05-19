## ADDED Requirements

### Requirement: Continuity-sensitive responses flows emit explicit operator diagnostics
When the proxy resolves or fails closed a continuity-sensitive follow-up request, the system MUST emit structured diagnostics that let operators determine how continuity ownership was resolved or why the proxy returned a retryable masked error.

#### Scenario: owner resolution source is recorded for a previous-response follow-up
- **WHEN** a websocket, HTTP fallback, or HTTP bridge follow-up request includes `previous_response_id`
- **AND** the proxy resolves the required owner account from a continuity source such as a local bridge session, owner cache, or request-log lookup
- **THEN** the system emits a structured diagnostic describing the continuity surface, source, and outcome
- **AND** the diagnostic does not expose the raw `previous_response_id`

#### Scenario: fail-closed continuity masking is recorded
- **WHEN** the proxy rewrites or returns a retryable continuity error because owner metadata is unavailable, continuity state is lost, or the pinned owner account is unavailable
- **THEN** the system emits a structured diagnostic describing the continuity surface and fail-closed reason
- **AND** Prometheus counters record the low-cardinality source or reason labels for that decision
