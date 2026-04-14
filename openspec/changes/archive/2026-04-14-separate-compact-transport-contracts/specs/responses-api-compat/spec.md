## ADDED Requirements

### Requirement: Compact responses remain canonical opaque context windows
Successful `/backend-api/codex/responses/compact` and `/v1/responses/compact` requests MUST return the upstream compact payload as the canonical next context window. The service MUST preserve provider-owned compact payload contents without pruning, reordering, or rewriting returned context items beyond generic JSON serialization.

#### Scenario: Compact response includes retained items and encrypted compaction state
- **WHEN** the upstream compact response returns a window that includes retained context items plus provider-owned compaction state such as encrypted content
- **THEN** the service returns that window unchanged to the client

#### Scenario: Compact response object shape differs from normal Responses
- **WHEN** the upstream compact response uses a provider-owned compact object shape instead of a standard `object: "response"` payload
- **THEN** the service returns that compact object shape unchanged instead of coercing it into a normal Responses payload

### Requirement: Compact transport fails closed without surrogate fallback
If direct upstream compact execution fails before a valid compact JSON payload is received, the service MUST keep the request inside the compact contract. The service MUST NOT silently substitute `/codex/responses`, reconstruct compact output from streamed Responses events, or synthesize a compact window locally. The service MAY perform a bounded retry only against the same upstream compact contract when the failure occurs in a provably safe transport phase before a valid compact JSON payload is accepted.

#### Scenario: Direct compact transport fails before response body is available
- **WHEN** the upstream `/codex/responses/compact` call times out, disconnects, or otherwise fails before yielding a valid compact JSON payload
- **THEN** the service may retry only `/codex/responses/compact` within a bounded retry budget
- **AND** it does not attempt a surrogate `/codex/responses` request

#### Scenario: Direct compact transport gets a safe retryable upstream failure
- **WHEN** the upstream `/codex/responses/compact` call fails with `401`, `502`, `503`, or `504` before a valid compact JSON payload is accepted
- **THEN** the service may retry only `/codex/responses/compact`
- **AND** it preserves the request's established compact routing and affinity semantics except for refreshed provider identity on `401`
- **AND** it does not call `/codex/responses`

#### Scenario: Direct compact response payload is invalid
- **WHEN** the upstream `/codex/responses/compact` call returns a non-error payload that is not valid compact JSON for pass-through
- **THEN** the service returns an upstream error to the client
- **AND** it does not retry via `/codex/responses`
- **AND** it does not synthesize or reconstruct a replacement compact window
