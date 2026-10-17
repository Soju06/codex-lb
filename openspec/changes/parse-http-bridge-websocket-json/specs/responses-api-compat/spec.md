## ADDED Requirements

### Requirement: HTTP bridge parses complete WebSocket JSON documents

The HTTP Responses bridge MUST interpret upstream text messages as complete
JSON objects, independent of indentation, LF/CRLF formatting, or surrounding
JSON whitespace. Native-interpreted and opaque frames MUST have equivalent
classification and correlation. Conversion to SSE MUST retain a complete valid
JSON object. Actual HTTP SSE parsing MUST remain unchanged.
Malformed/non-object frames and objects containing non-finite floats MUST NOT
be forwarded downstream. Native-interpreted payloads MUST enforce the same
finite-number policy as opaque frames, including nested values.

#### Scenario: Early multiline error is delivered

- **GIVEN** one request awaits response creation
- **WHEN** upstream sends a formatted JSON validation error
- **THEN** the client receives the original code, type, and parameter through the existing HTTP 400 or committed SSE error contract
- **AND** pending state settles without a formatting-induced retry, timeout, quarantine, or account penalty
- **AND** the next valid request can use the bridge

#### Scenario: Formatted successful lifecycle

- **WHEN** lifecycle and output messages contain indentation or CRLF
- **THEN** the bridge preserves their event ordering, response correlation, and valid downstream SSE

#### Scenario: Invalid data remains unclassified

- **WHEN** a frame is malformed JSON, a non-object JSON value, or SSE-framed text
- **THEN** it does not manufacture response identity or terminal evidence
- **AND** rejected text is not forwarded as a raw downstream SSE block
- **AND** existing pending-request safety remains authoritative

#### Scenario: Non-finite values are rejected on either transport path

- **WHEN** an opaque frame contains NaN, Infinity, -Infinity, or a numeric exponent that decodes to a non-finite float
- **OR** a native-interpreted payload contains a non-finite float at any nesting depth
- **THEN** neither its original text nor its parsed values are forwarded downstream
- **AND** it does not finish or identify a pending response
- **AND** a later valid lifecycle or validation-error frame is still processed normally

#### Scenario: Finite numbers and text are preserved

- **WHEN** a valid object contains finite floats, integers, or strings mentioning NaN or Infinity
- **THEN** its content and ordinary event classification remain unchanged
