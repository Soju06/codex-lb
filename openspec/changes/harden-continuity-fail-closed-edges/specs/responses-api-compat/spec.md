## ADDED Requirements

### Requirement: Continuity-dependent Responses follow-ups fail closed with retryable errors
When a Responses follow-up depends on previously established continuity state, the service MUST return a retryable continuity error if that continuity cannot be reconstructed safely. The service MUST NOT expose raw `previous_response_not_found` for bridge-local metadata loss or similar internal continuity gaps.

#### Scenario: HTTP bridge loses local continuity metadata for a follow-up request
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` follow-up request depends on `previous_response_id` or a hard continuity turn-state
- **AND** the bridge cannot reconstruct the matching live continuity state from local or durable metadata
- **THEN** the service returns a retryable OpenAI-format error
- **AND** the error code is not `previous_response_not_found`

#### Scenario: in-flight bridge follower loses continuity while waiting on the same canonical session
- **WHEN** a follow-up request waits on an in-flight HTTP bridge session for the same hard continuity key
- **AND** the bridge still cannot reconstruct safe continuity state once the leader finishes
- **THEN** the service returns a retryable OpenAI-format error
- **AND** the error code is not `previous_response_not_found`

### Requirement: Hard continuity owner lookup fails closed
When a request depends on hard continuity ownership, the service MUST fail closed if owner or ring lookup errors prevent safe pinning. The service MUST NOT continue with local recovery or account selection that bypasses hard owner enforcement.

#### Scenario: websocket previous-response owner lookup errors
- **WHEN** a websocket or HTTP fallback follow-up request includes `previous_response_id`
- **AND** owner lookup errors prevent the proxy from determining the required owner account
- **THEN** the service returns a retryable OpenAI-format error
- **AND** it does not continue the request on an unpinned account

#### Scenario: bridge owner or ring lookup errors for hard continuity keys
- **WHEN** an HTTP bridge request uses a hard continuity key such as turn-state, explicit session affinity, or `previous_response_id`
- **AND** owner or ring lookup errors prevent the proxy from proving the correct bridge owner
- **THEN** the service returns a retryable OpenAI-format error
- **AND** it does not create or recover a local bridge session on the current replica
