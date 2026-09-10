## MODIFIED Requirements

### Requirement: Client-to-LB routing hints remain hop-local

The service MUST discard inbound `x-codex-routing-hint` values case-insensitively.
Proxy-routed subscription Responses requests with a known model MUST synthesize a
new hint from the final model and service tier when opening an HTTP request or
WebSocket handshake. Inbound values MUST
NOT determine that hint. Inbound LB API-key authentication MUST NOT prevent
synthesis for a selected subscription account. Non-subscription transports MUST
NOT synthesize a Codex-backend hint.

#### Scenario: Inbound HTTP hint is replaced
- **GIVEN** an inbound request advertises a different model or tier in its hint
- **WHEN** a subscription-account Responses HTTP request is built
- **THEN** any synthesized hint MUST reflect the final outbound body

#### Scenario: Inbound WebSocket hint is discarded
- **GIVEN** an inbound handshake includes any case spelling of the hint header
- **WHEN** upstream WebSocket handshake headers are built
- **THEN** the inbound value MUST NOT be forwarded
- **AND** any synthesized hint MUST use trusted request state

## ADDED Requirements

### Requirement: Subscription Responses synthesize final routing hints

Subscription-account Responses HTTP egress, transient WebSocket handshakes,
persistent WebSocket handshakes and HTTP fallback MUST synthesize
`x-codex-routing-hint: model=<model>;tier=<tier>` from the final normalized
model and service tier. With no tier the hint MUST contain only `model=<model>`.
A preconnect without a request model MUST omit the hint. Reusing an open
WebSocket MUST NOT reconnect solely to replace its handshake hint. Synthesis
MUST NOT alter request bodies, entitlement checks or actual response tiers.

#### Scenario: Fast account request through either public API
- **WHEN** a subscription request selects Fast through the backend or v1 route
- **THEN** its outbound body and synthesized hint MUST use priority
- **AND** caller API-key authentication MUST NOT disable hint synthesis

#### Scenario: WebSocket fallback retains request routing
- **WHEN** a subscription WebSocket attempt falls back to HTTP
- **THEN** the HTTP hint MUST use the same final model and service tier

#### Scenario: Custom provider does not gain a backend hint
- **WHEN** a request is dispatched to a non-subscription model source
- **THEN** no Codex-backend routing hint MUST be synthesized

### Requirement: Subscription compaction propagates routing hints

Compaction egress for proxy-routed subscription client requests MUST synthesize
`x-codex-routing-hint: model=<model>;tier=<tier>` from the final normalized request.
When no tier remains after policy enforcement, the hint MUST contain only
`model=<model>`. Eligibility MUST use selected subscription provenance, including
when the optional ChatGPT account-ID header is absent. Hint synthesis MUST NOT
change compaction input, usage, retries, or the response's actual service tier.

#### Scenario: Fast compaction uses the canonical tier
- **WHEN** a subscription compaction request selects the Fast alias
- **THEN** its outbound body and hint MUST both use `priority`

#### Scenario: Ultrafast compaction preserves the requested tier
- **WHEN** an eligible subscription compaction request selects `ultrafast`
- **THEN** its outbound body and hint MUST both use `ultrafast`

#### Scenario: Prohibited Fast does not leak through the hint
- **WHEN** policy removes a subscription compaction request's Fast tier
- **THEN** its outbound body MUST omit that tier
- **AND** its hint MUST contain only the final model

#### Scenario: Non-subscription compaction does not synthesize a hint
- **WHEN** compaction transport is invoked without subscription provenance
- **THEN** it MUST NOT synthesize or forward a routing hint
