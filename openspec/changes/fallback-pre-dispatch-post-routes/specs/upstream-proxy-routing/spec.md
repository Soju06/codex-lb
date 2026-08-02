# upstream-proxy-routing Delta

## ADDED Requirements

### Requirement: Routed pre-dispatch POST failures may use the next pool endpoint

When a routed HTTP request fails before the upstream request can be dispatched, and the client has typed `retryable_same_contract` provenance for that failure, the Codex client MUST try the next active endpoint in the same resolved pool when one exists. This rule MUST apply to non-idempotent methods, including streaming `POST` requests. TLS verification failures MUST NOT qualify for this non-idempotent fallback.

#### Scenario: Streaming POST fails before dispatch on the primary endpoint

- **GIVEN** a resolved pool whose primary HTTP endpoint resets its TLS connection before response headers
- **AND** the client classifies the failure as a pre-dispatch connector failure with `retryable_same_contract=True`
- **WHEN** the client sends a streaming `POST` request
- **THEN** it MUST try the next endpoint in the same pool
- **AND** it MUST return the fallback response when that endpoint succeeds
- **AND** route metadata MUST identify the fallback endpoint and `fallback_used=True`

#### Scenario: POST failure after dispatch does not replay

- **GIVEN** a routed `POST` request received response headers or failed while reading the response body
- **WHEN** the request fails
- **THEN** the client MUST NOT try another pool endpoint

#### Scenario: TLS verification failure does not qualify for POST fallback

- **GIVEN** a routed `POST` request fails with a TLS certificate verification error
- **WHEN** the primary endpoint fails
- **THEN** the client MUST return the transport error without replaying the request on another endpoint
