## ADDED Requirements

### Requirement: Codex exposes an explicit Daybreak Blue routing profile

The published Codex client configuration MUST keep the ordinary `codex-lb` provider free of `X-Codex-LB-Required-Capability` and MUST define a separate `codex-lb-daybreak-blue` provider that sources its proxy API key from `CODEX_LB_API_KEY` and whose static headers contain exactly one `X-Codex-LB-Required-Capability: trusted_cyber` carrier. A machine-local `daybreak-blue` profile file MUST select that provider and the canonical `gpt-5.6-sol` model. Activating the Daybreak profile MUST be explicit and MUST NOT modify the default provider selection.

#### Scenario: Daybreak profile constrains the first attempt

- **WHEN** an authenticated direct Responses WebSocket turn starts through the published `daybreak-blue` profile
- **THEN** capability ingress receives exactly one `trusted_cyber` carrier before the first account-selection call
- **AND** the first and every later selection requires an eligible security-work-authorized account
- **AND** no ordinary account receives an upstream attempt

#### Scenario: Ordinary provider remains unchanged

- **WHEN** an authenticated direct Responses WebSocket turn starts through the published ordinary `codex-lb` provider
- **THEN** the request contains no required-capability carrier
- **AND** the first account-selection call remains unconstrained by trusted-cyber routing

#### Scenario: Profile does not grant authorization

- **WHEN** the Daybreak profile is selected without an authenticated proxy request or without an eligible security-work-authorized account
- **THEN** the existing capability-ingress or empty-capable-pool contract fails closed
- **AND** routing does not fall back to an ordinary account
