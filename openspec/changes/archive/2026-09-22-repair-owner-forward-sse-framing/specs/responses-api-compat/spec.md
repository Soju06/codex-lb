## ADDED Requirements

### Requirement: Owner-forward HTTP streams preserve SSE event boundaries

The owner-forward HTTP receiver MUST dispatch each complete SSE event delimited
by two consecutive CR, LF, or CRLF line endings, including mixed endings,
without waiting for connection close. Chunk boundaries MUST NOT change payload
text or cause a CRLF continuation byte to become part of the next event. Invalid
UTF-8 bytes in complete events or the final unterminated block MUST decode with
replacement characters instead of aborting the relay. Valid UTF-8 characters
split across chunks MUST remain intact. Existing idle and request-budget timeout
classification MUST remain unchanged.

#### Scenario: Non-LF framing streams incrementally

- **WHEN** an owner forwards multiple events separated by CRLF, CR, or mixed CR/LF blank lines
- **THEN** the origin dispatches each event before EOF
- **AND** the final `response.completed` remains separately parseable

#### Scenario: Chunk boundaries preserve text and event identity

- **WHEN** network chunks split a multi-byte UTF-8 character or a CRLF separator
- **THEN** decoded payload text remains intact
- **AND** the next event has no residual separator byte prefixed to its fields

#### Scenario: Malformed UTF-8 is replaced

- **WHEN** an event or the final unterminated block contains invalid UTF-8
- **THEN** the receiver replaces the invalid bytes with U+FFFD
- **AND** continues delivering subsequent events when present
