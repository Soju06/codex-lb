## ADDED Requirements

### Requirement: Optional source metrics cannot interrupt valid forwarding

Source usage parsing MUST preserve reported nonnegative integer reasoning tokens and reject boolean or database-integer-out-of-range token values. Missing reasoning details MUST remain unknown. Optional timing parsing MUST reject non-finite, negative, boolean, overflowing and database-integer-out-of-range values, including overflow when adding individually finite timings, without interrupting an otherwise valid upstream response. Streamed usage/metrics MUST parse complete SSE events, combining multiple data lines and preserving framing across chunk-split CRLF boundaries. Forwarded bytes MUST remain unchanged.

#### Scenario: Overflowing timing sum is ignored

- **GIVEN** individually finite source TTFT and generation timing whose sum overflows
- **WHEN** the response is parsed
- **THEN** optional timing remains absent and response forwarding completes normally

#### Scenario: Multi-line usage event split at CRLF

- **GIVEN** a valid SSE usage/metrics event contains several data lines and a network chunk ends between CR and LF
- **WHEN** stream parsing completes
- **THEN** usage, reasoning and timing match the equivalent single-chunk event

#### Scenario: Unrepresentable source token count

- **GIVEN** an upstream reports a token count outside the request-log database integer range
- **WHEN** usage is parsed
- **THEN** that usage is treated as unavailable rather than causing request-log persistence to overflow
- **AND** the existing fail-closed behavior for API-key limits requiring usage is preserved

## MODIFIED Requirements
