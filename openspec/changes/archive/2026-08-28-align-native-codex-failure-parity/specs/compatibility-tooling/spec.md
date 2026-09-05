## ADDED Requirements

### Requirement: Failure analysis covers end-to-end and egress legs

When Path A, Path B, and Path C captures are supplied, the analyzer MUST report
failure outcomes for both A/B (direct Codex versus Codex through codex-lb) and
B/C (codex-lb egress versus controlled origin). Each comparison MUST expose
status, normalized `Retry-After`, terminal class, completeness, incomplete
reason, and bounded network-error category for both sides. A differing
`Retry-After` value on corresponding HTTP failure turns MUST fail strict
comparison rather than remain informational.

#### Scenario: A/B outcome translation is visible

- **GIVEN** direct Codex ends with a network error and routed Codex receives a
  synthetic terminal failure
- **WHEN** the analyzer compares Path A and Path B
- **THEN** the report shows the lifecycle translation in an A/B failure table

#### Scenario: Retry hint mismatch fails strict parity

- **GIVEN** corresponding failure responses have different normalized
  `Retry-After` values
- **WHEN** strict comparison runs
- **THEN** the result includes a retry-hint mismatch and does not pass
