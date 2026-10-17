## ADDED Requirements

### Requirement: HTTP bridge counts explicit incomplete transport reasons

For an otherwise eligible pre-response HTTP-bridge attempt, the proxy MUST account a `response.incomplete` terminal with `incomplete_details.reason` equal to `stream_incomplete` and no explicit response error as one retry-circuit failure. It MUST apply existing duplicate and eligibility guards. An explicit response error MUST retain precedence. Neutral, unknown and missing reasons MUST NOT become transport failures. Downstream terminal payloads, request-log classification and account-health treatment MUST remain unchanged.

#### Scenario: Reason-only stream failure
- **WHEN** an eligible HTTP-bridge attempt receives a reason-only `stream_incomplete` terminal through either raw or interpreted upstream input
- **THEN** one retry-circuit failure is recorded
- **AND** repeating its terminal does not add another failure
- **AND** its downstream payload and account-health treatment remain unchanged

#### Scenario: Neutral reasons and explicit errors
- **WHEN** an incomplete terminal contains a neutral, unknown or missing reason, or an explicit response error
- **THEN** the existing explicit-error classification takes precedence when present
- **AND** the reason alone does not introduce a transport failure unless it is exactly `stream_incomplete`
- **AND** the request-log classification remains unchanged

#### Scenario: Existing attempt exclusions
- **WHEN** a reason-only stream failure belongs to an attempt excluded by existing circuit eligibility or duplicate guards
- **THEN** the terminal does not add a circuit failure
