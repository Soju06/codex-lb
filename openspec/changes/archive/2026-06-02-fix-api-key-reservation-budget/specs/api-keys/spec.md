## ADDED Requirements

### Requirement: Request-aware API-key usage reservations

API-key usage reservation admission MUST reserve a bounded request-aware budget instead of an unconditional fixed 8192 input-token plus 8192 output-token pre-charge for every request. The reservation budget MUST be used only for admission and in-flight accounting; final usage accounting MUST continue to settle to the authoritative completed request usage and service-tier pricing.

For token limits, admission MUST reserve from the request input and output token budgets. The input budget MAY be estimated from self-contained request payloads, while opaque upstream context MUST fall back to a conservative input budget. The output budget MUST use a bounded system default unless codex-lb can verify that a client-provided output cap is actually enforced upstream. For `cost_usd` limits, admission MUST compute the reservation cost from the same input and output token budgets and the effective request service tier. Reservation finalization MUST adjust every applicable reserved value to actual completed usage exactly once, including limits whose admission reservation was zero.

#### Scenario: Concurrent priority lanes do not require 8 × 8192 output-token headroom

- **WHEN** an API key has a `cost_usd` limit with enough remaining value for the bounded request-aware reservations
- **AND** eight `gpt-5.5` requests using `service_tier = "priority"` are admitted concurrently
- **THEN** the proxy allows all eight reservations instead of rejecting a lane solely because the old 8192-output-token pre-charge would exceed the limit

#### Scenario: Opaque input uses conservative input fallback

- **WHEN** a request references input that the proxy cannot size locally, such as `previous_response_id`, `conversation`, `input_file`, or `input_image`
- **THEN** API-key admission uses the conservative default input-token reservation budget for input tokens
- **AND** final accounting still settles to actual completed usage

#### Scenario: Zero-reservation limits still settle actual usage

- **WHEN** API-key admission records a zero-delta reservation item for an applicable limit
- **AND** the request completes with non-zero actual usage for that limit
- **THEN** reservation finalization increments the limit by the actual usage instead of skipping the limit
