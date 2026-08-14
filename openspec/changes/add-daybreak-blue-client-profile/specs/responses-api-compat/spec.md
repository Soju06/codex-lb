## ADDED Requirements

### Requirement: Codex exposes an explicit Daybreak Blue routing profile

The published Codex client configuration MUST keep the ordinary `codex-lb` provider free of `X-Codex-LB-Required-Capability` and MUST define a separate `codex-lb-daybreak-blue` provider that sources its proxy API key from `CODEX_LB_API_KEY` and whose static headers contain exactly one `X-Codex-LB-Required-Capability: trusted_cyber` carrier. A machine-local `daybreak-blue` profile file MUST select that provider and the canonical `gpt-5.6-sol` model. Activating the Daybreak profile MUST be explicit and MUST NOT modify the default provider selection. Direct Responses WebSocket ingress MUST require a valid proxy API key whenever the capability header is present, even when deployment-wide API-key auth is disabled, and MUST preserve the existing authentication behavior when the header is absent. Any capability-bearing HTTP request on an external provider-bound route that can select or forward an upstream account, including Responses, compact, thread-goal, Codex-control, opportunistic admission, warmup, files, transcription, chat, Images, and reset-credit consume routes, MUST authenticate the carrier and MUST then fail with `400 required_capability_transport_unsupported` before routing or upstream dispatch. Typed JSON Responses, compact, Chat Completions, Images generations, and reset-credit consume routes MUST apply that authenticate-then-deny check before FastAPI decodes the request body. A capability-bearing non-Responses WebSocket MUST apply the same authenticate-then-deny contract before owner lookup or upstream connection. Authenticated model-catalog, local API-key usage, and reset-credit listing requests MAY remain available because they perform no upstream account routing. The separate WHAM namespace MUST retain ordinary forwarding after the shared capability-header authentication rule and MUST NOT apply the Responses transport denial. Headerless ingress MUST retain its existing behavior. A legitimately forwarded internal bridge request MUST strip the capability carrier. If a carrier is appended to an otherwise valid signed internal bridge request, the target MUST authenticate it and fail closed before legacy-anchor validation, account selection, or upstream dispatch.

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

#### Scenario: Other provider-bound HTTP routes fail closed

- **WHEN** the authenticated Daybreak carrier reaches a thread-goal, Codex-control, opportunistic-admission, warmup, files, transcription, chat, Images, or reset-credit consume HTTP route
- **THEN** ingress returns `400 required_capability_transport_unsupported`
- **AND** no model source, account, reservation, owner binding, or upstream attempt is selected

#### Scenario: Non-Responses WebSocket fails closed

- **WHEN** the authenticated Daybreak carrier reaches a Live or Realtime WebSocket route
- **THEN** ingress returns `400 required_capability_transport_unsupported` during the handshake
- **AND** no call owner is resolved and no upstream connection is opened

#### Scenario: Signed internal forward cannot reintroduce the carrier

- **WHEN** an otherwise valid signed internal Responses bridge request arrives with the Daybreak carrier appended
- **THEN** ingress authenticates the proxy API key and returns `400 required_capability_transport_unsupported`
- **AND** no legacy bridge anchor, account selection, or upstream dispatch occurs

#### Scenario: Model-catalog initialization remains available

- **WHEN** the authenticated Daybreak provider requests `/backend-api/codex/models`
- **THEN** ingress returns the local model catalog
- **AND** no account is selected and no upstream request is made

#### Scenario: Profile does not grant authorization

- **WHEN** the Daybreak profile is selected without an authenticated proxy request or without an eligible security-work-authorized account
- **THEN** the existing capability-ingress or empty-capable-pool contract fails closed
- **AND** routing does not fall back to an ordinary account
