## ADDED Requirements

### Requirement: Routed native SSE options preserve transport ownership

Unbuffered routed HTTP requests MAY supply typed native SSE options.
`CodexClient` MUST pass these options unchanged to each native endpoint attempt
and MUST reject their use with buffered response consumption before dispatch.
It MUST NOT forward native-only options to a Python HTTP client. Each native
attempt MUST use only its resolved proxy endpoint, and successful results MUST
retain selected route metadata and native framed-response consumption.

#### Scenario: Pre-dispatch endpoint fallback preserves framing limits

- **WHEN** an unbuffered routed native POST has a confirmed replay-safe connect failure at its first endpoint
- **THEN** the next eligible endpoint receives the same framing limits
- **AND** the result records that endpoint and fallback use

#### Scenario: Missing helper uses the same resolved Python route

- **WHEN** the helper is unavailable before dispatch for a routed SSE request
- **THEN** Python transport uses the resolved endpoint and ordinary SSE parser
- **AND** native-only SSE options do not reach the HTTP client

#### Scenario: Buffered operation cannot select framed consumption

- **WHEN** a caller supplies native SSE options with buffered response consumption
- **THEN** the request fails before either native or Python dispatch
