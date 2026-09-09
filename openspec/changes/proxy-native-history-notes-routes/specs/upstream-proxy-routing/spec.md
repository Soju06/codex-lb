## ADDED Requirements

### Requirement: Account-local Codex control operations remain account-affine

Codex-control operations that carry account-local state MUST use their
protocol-defined session identity with the existing account-affinity selection
path. When the operation has no upstream idempotency contract, the proxy MUST
NOT retry or fail over it on a different account after account selection.

#### Scenario: Native history context selects the existing session owner

- **GIVEN** a native history-marked Codex Responses request established hard history affinity for
  `session_123` on account A
- **AND** an authenticated native history request has
  `context.session_id = session_123` and no session header
- **WHEN** the proxy selects an upstream account
- **THEN** it uses the same hard history-session identity
- **AND** it does not select an unrelated eligible account B
