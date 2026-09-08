## ADDED Requirements

### Requirement: HTTP bridge safety-policy failures do not poison continuity

When an HTTP Responses bridge request receives a terminal pre-output
`response.failed` or `error` event that qualifies as an account-neutral
`misalignment_policy_violation` safety-policy rejection, the bridge MUST NOT
record a retry-circuit failure for that request lifecycle and MUST NOT use the
rejection as durable-anchor poison evidence. The bridge MUST still settle the
request normally and MUST forward the original terminal event, including its
error code and message, without replacing or suppressing it.

#### Scenario: Pre-output safety block bypasses the retry circuit

- **GIVEN** an HTTP bridge request has emitted no response events
- **WHEN** upstream terminates it with a qualifying safety-policy
  `response.failed` event
- **THEN** the bridge does not increment or open the retry circuit for the
  continuity key
- **AND** the failure is not counted as anchor-poison evidence

#### Scenario: Original safety failure reaches the client

- **WHEN** the HTTP bridge receives a qualifying safety-policy
  `response.failed` event
- **THEN** the downstream stream receives the original `response.failed` event
- **AND** its error code remains `misalignment_policy_violation`
- **AND** its safety-system error message is preserved
