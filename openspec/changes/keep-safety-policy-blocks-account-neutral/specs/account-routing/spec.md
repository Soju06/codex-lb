## ADDED Requirements

### Requirement: Safety-policy request blocks are account-health neutral

When upstream rejects a request with error code
`misalignment_policy_violation`, HTTP status 400 when a status is known, and a
message beginning `This request was blocked by our safety systems.`, the proxy
MUST treat the rejection as request-scoped and MUST NOT mutate the selected
account's health. It MUST NOT record a transient account error, rate-limit
penalty, quota penalty, or permanent failure for that account. A different
code, a known non-400 status, or an unrelated message MUST keep its existing
account-health handling. The health-neutral decision MUST NOT change the
failure classification or client-visible error.

#### Scenario: Safety-policy rejection leaves account health untouched

- **GIVEN** account A is selected for a request
- **WHEN** upstream returns `misalignment_policy_violation` with HTTP status
  400 or no separately available status and the safety-system block message
- **THEN** the proxy does not increment account A's transient error count and
  does not mark it rate-limited, quota-exceeded, or permanently failed
- **AND** the failure remains classified as non-retryable

#### Scenario: Similar non-policy failure keeps its health handling

- **WHEN** an upstream failure has a different code, a known non-400 status,
  or a message that does not identify the safety-system block
- **THEN** the proxy does not classify it as an account-neutral safety-policy
  rejection
- **AND** its existing account-health handling remains in effect
