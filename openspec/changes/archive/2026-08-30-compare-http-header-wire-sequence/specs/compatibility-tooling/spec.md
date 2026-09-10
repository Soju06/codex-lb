## ADDED Requirements

### Requirement: Captures preserve privacy-safe HTTP header sequence evidence

The traffic capture addon MUST record request header field names in observed
serialization order, including duplicate occurrences and original casing, and
MUST NOT add header values to this sequence evidence. The analyzer MUST compare
normalized field-name order and exact casing independently for Path A and Path
C. Missing sequence evidence MUST be reported as unobserved rather than pass.
The comparison MUST remain informational and MUST NOT claim HPACK or HTTP/2
frame parity from decoded headers.

#### Scenario: Header order and casing match

- **GIVEN** Path A and Path C contain ordered request header-name evidence
- **WHEN** normalized order and exact casing are equal
- **THEN** both header sequence dimensions match

#### Scenario: Casing differs without an order change

- **GIVEN** Path A records `content-type` and Path C records `Content-Type` in
  the same position
- **WHEN** the report compares header sequence evidence
- **THEN** normalized order matches
- **AND** exact casing does not match

#### Scenario: Evidence is absent

- **GIVEN** either capture predates header sequence metadata
- **WHEN** the report compares the paths
- **THEN** both header sequence dimensions are unobserved
- **AND** missing evidence is not promoted to a match
