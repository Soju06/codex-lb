## ADDED Requirements

### Requirement: Trusted cyber intent narrows the existing account pool

Account routing MUST constrain an authenticated direct Responses WebSocket
turn requiring `trusted_cyber` by passing
`require_security_work_authorized=True` to the canonical selector before the
first upstream attempt and every later retry. The selector MUST apply the
constraint only to accounts already permitted by API-key, account, model,
service-tier, ownership, health, quota, affinity, concurrency, and failover
rules. Routing MUST NOT add an account, change the configured strategy, rebind
an owner, or fall back to an ordinary account.

#### Scenario: First attempt uses the capable pool
- **WHEN** an authenticated direct WebSocket turn establishes `trusted_cyber`
- **THEN** its first account-selection call requires a
  security-work-authorized account
- **AND** no ordinary account receives an upstream attempt

#### Scenario: Empty capable pool fails closed
- **WHEN** a required turn has no eligible security-work-authorized account
- **THEN** selection returns the existing typed
  `no_security_work_authorized_accounts` error
- **AND** its advisory states that no ordinary-account fallback occurred
- **AND** an earlier reactive or account/model error cannot replace that typed
  capability-routing result
- **AND** ordinary routing is not attempted

#### Scenario: Ordinary routing is unchanged
- **WHEN** an authenticated direct WebSocket turn has neither a trusted signal
  nor required lineage
- **THEN** selection receives the same scope, strategy, ownership, admission,
  and retry inputs as before this change
