## ADDED Requirements

### Requirement: Codex exposes an explicit Daybreak Blue routing profile

The published Codex client configuration MUST keep the ordinary `codex-lb` provider free of `X-Codex-LB-Required-Capability` and MUST define a separate `codex-lb-daybreak-blue` provider that sources its proxy API key from `CODEX_LB_API_KEY` and whose static headers contain exactly one `X-Codex-LB-Required-Capability: trusted_cyber` carrier. A machine-local `daybreak-blue` profile file MUST select that provider and the canonical `gpt-5.6-sol` model. Activating the Daybreak profile MUST be explicit and MUST NOT modify the default provider selection. Direct Responses WebSocket ingress MUST require a valid proxy API key whenever the capability header is present, even when deployment-wide API-key auth is disabled, and MUST preserve the existing authentication behavior when the header is absent. External HTTP Responses and compact ingress MUST authenticate any request carrying the capability header and MUST then fail with `400 required_capability_transport_unsupported` before routing or upstream dispatch. Headerless HTTP ingress MUST retain its existing behavior.

#### Scenario: Daybreak profile constrains the first attempt

- **WHEN** an authenticated direct Responses WebSocket turn starts through the published `daybreak-blue` profile
- **AND** deployment-wide proxy API-key auth is disabled
- **THEN** capability ingress receives exactly one `trusted_cyber` carrier before the first account-selection call
- **AND** capability ingress validates the profile's proxy API key before accepting the carrier
- **AND** the first and every later selection requires an eligible security-work-authorized account
- **AND** no ordinary account receives an upstream attempt

#### Scenario: Ordinary provider remains unchanged

- **WHEN** an authenticated direct Responses WebSocket turn starts through the published ordinary `codex-lb` provider
- **THEN** the request contains no required-capability carrier
- **AND** capability ingress does not impose a new per-request API-key requirement
- **AND** the first account-selection call remains unconstrained by trusted-cyber routing

#### Scenario: Daybreak HTTP downgrade fails closed before routing

- **WHEN** Codex retains the Daybreak provider's capability carrier while falling back to an HTTP Responses or compact request
- **AND** the request supplies the profile's valid proxy API key
- **THEN** ingress returns `400 required_capability_transport_unsupported`
- **AND** no model source, account, reservation, bridge, or upstream attempt is selected
- **AND** the request is not replayed through ordinary routing

#### Scenario: Daybreak HTTP downgrade authenticates before transport denial

- **WHEN** a capability-bearing HTTP Responses request omits or supplies an invalid proxy API key
- **THEN** ingress returns the existing `401 invalid_api_key` authentication error
- **AND** no routing or upstream attempt occurs

#### Scenario: Ordinary HTTP routing remains unchanged

- **WHEN** the published ordinary provider sends an HTTP Responses request without the capability carrier
- **THEN** ingress does not impose the Daybreak per-request authentication or transport denial
- **AND** existing ordinary HTTP routing behavior is preserved

#### Scenario: Profile does not grant authorization

- **WHEN** the Daybreak profile is selected without an authenticated proxy request or without an eligible security-work-authorized account
- **THEN** the existing capability-ingress or empty-capable-pool contract fails closed
- **AND** routing does not fall back to an ordinary account
