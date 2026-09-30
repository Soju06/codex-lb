## ADDED Requirements

### Requirement: Disclose unquantified upstream quota scope

Client usage responses SHALL disclose reported unquantified Pro Max windows
without fabricating a weighted whole-pool percentage. Positive legacy
upstream-limit entries SHALL remain known-capacity subtotals.
`upstream_limits_unquantified_windows` SHALL identify the affected duration
labels only when upstream-limit disclosure is permitted.

#### Scenario: Pro Max pool with usage disclosure
- **WHEN** a permitted account pool contains a reported Pro Max weekly window
- **THEN** its whole-pool weekly remaining percentage is null
- **AND** its weekly unquantified-account count is positive
- **AND** disclosed upstream-limit coverage includes `7d`

#### Scenario: Hidden or out-of-scope usage
- **WHEN** usage privacy or API-key section settings prohibit upstream limits,
  or the key is not assigned the Pro Max account
- **THEN** the response does not disclose that account through coverage metadata
- **AND** key-owned limits retain their existing behavior
