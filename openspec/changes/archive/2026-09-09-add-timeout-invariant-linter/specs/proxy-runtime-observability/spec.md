## ADDED Requirements

### Requirement: Timeout-invariant violations are diagnosable

Timeout-invariant validation diagnostics SHALL include the rule id, left-hand
setting or expression and value, relation, right-hand setting or expression and
value, rationale, and code anchors. Diagnostics SHALL avoid request payloads,
API keys, access tokens, raw affinity keys, account emails, and other
high-cardinality runtime identifiers. Diagnostics SHALL describe startup
validation of `Settings` and imported constants only, not per-request overrides,
runtime clamps, or runtime-derived effective values.

#### Scenario: Violation log names the invariant

- **WHEN** startup timeout-invariant validation observes a violated rule
- **THEN** the CRITICAL log includes that rule id and rationale
- **AND** the log contains no request payload, API key, access token, raw
  affinity key, or account email
