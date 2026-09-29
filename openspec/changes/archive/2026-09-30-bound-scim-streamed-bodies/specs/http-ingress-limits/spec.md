## ADDED Requirements

### Requirement: SCIM body admission stops at the route limit

SCIM resource writes MUST enforce their 64 KiB body limit while consuming
the request stream. An oversized request MUST stop receiving at the first
chunk that exceeds that limit and MUST return the SCIM 413 envelope without
writing an account. Buffered body bytes MUST NOT exceed the route limit.
The existing declared-length precheck MUST remain in effect. A valid body
split across chunks, including a body exactly at the limit, MUST remain valid.

#### Scenario: Stream exceeds the limit without a declared length

- **GIVEN** a SCIM body arrives in several chunks without Content-Length
- **WHEN** received bytes first exceed 64 KiB
- **THEN** the API returns SCIM 413 without reading subsequent chunks
- **AND** it writes no account

#### Scenario: Valid resource arrives in fragments

- **WHEN** a valid resource arrives in several chunks totaling at most 64 KiB
- **THEN** the API parses the complete resource normally
