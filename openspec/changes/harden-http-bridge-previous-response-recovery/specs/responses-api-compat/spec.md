### ADDED Requirements

### Requirement: HTTP bridge recovers continuity from previous_response_id when live session is known
When handling HTTP `/v1/responses` and `/backend-api/codex/responses`, the service MUST maintain an in-memory mapping from emitted upstream `response.id` values to the owning live HTTP bridge session (scoped by API key identity). For requests that include `previous_response_id`, the service MUST attempt to recover and reuse that mapped live bridge session before returning a continuity loss error.

#### Scenario: previous_response_id recovers continuity when request affinity key drifts
- **WHEN** a prior HTTP bridged request emitted a `response.id`
- **AND** a follow-up HTTP request includes that value as `previous_response_id`
- **AND** the follow-up request's bridge affinity key differs from the prior request
- **THEN** the service reuses the mapped live bridge session for the follow-up request
- **AND** the request succeeds without returning `previous_response_not_found`

#### Scenario: stale previous_response_id mapping does not bypass fail-closed behavior
- **WHEN** a follow-up HTTP request includes `previous_response_id`
- **AND** the mapped bridge session is closed, missing, or otherwise inactive
- **THEN** the service removes the stale mapping
- **AND** the service continues the existing fail-closed behavior by returning `previous_response_not_found`
