## ADDED Requirements

### Requirement: Controlled origin can inject bounded failure scenarios

The controlled traffic origin MUST keep success as its default and MUST accept
an operator-selected process scenario for HTTP 429, HTTP 503, delayed HTTP
response, incomplete SSE, WebSocket handshake rejection, or incomplete
WebSocket response. Scenario responses MUST NOT reflect request content or
credentials. Delay duration MUST be finite and validated. Request data MUST
NOT be able to select or alter the active scenario.

#### Scenario: Operator selects an HTTP rate limit

- **WHEN** the fixture starts with the HTTP 429 scenario
- **THEN** a Responses HTTP request receives status 429 and a deterministic
  `Retry-After` hint
- **AND** the response contains no request content or credential value

#### Scenario: SSE terminates before a terminal event

- **WHEN** the fixture starts with the incomplete SSE scenario
- **THEN** it emits `response.created` and ends without a terminal Responses
  event
- **AND** the analyzer marks the turn incomplete

#### Scenario: WebSocket fails before and after dispatch

- **WHEN** the fixture uses WebSocket rejection
- **THEN** it rejects the handshake before accepting a Responses turn
- **AND WHEN** it uses incomplete WebSocket response
- **THEN** it accepts one `response.create`, emits `response.created`, and
  closes without a terminal event

### Requirement: Traffic failure evidence is explicit and privacy-safe

The capture addon MUST write a record for a targeted HTTP flow that ends in a
transport error before the ordinary response hook completes. It MUST retain
the request semantic projection, partial status/headers/body metadata when
available, and a bounded error category. It MUST NOT retain raw exception
text, peer addresses, proxy addresses, credentials, or a duplicate record for
an exchange already captured by the response hook.

The analyzer MUST report, for each B/C turn, HTTP status, normalized
`Retry-After`, terminal class, completeness, incomplete reason, and network
error category. It MUST classify success, HTTP rejection, terminal failure,
transport incompleteness, and network error separately. This failure evidence
MUST be informational and MUST NOT turn an incomplete or mismatched lifecycle
into a strict parity pass.

#### Scenario: Upstream timeout has no response envelope

- **GIVEN** a targeted Responses HTTP flow ends with a timeout before response
  headers
- **WHEN** the addon writes the capture
- **THEN** it records the timeout category and redacted request semantics
- **AND** it does not store the raw timeout message

#### Scenario: Both legs expose failure outcomes

- **GIVEN** Path B and Path C contain corresponding failed turns
- **WHEN** the analyzer compares them
- **THEN** the report shows each leg's status, terminal class, completeness,
  retry hint, and network-error category
- **AND** a failure-to-failure translation is visible independently from the
  strict semantic result
