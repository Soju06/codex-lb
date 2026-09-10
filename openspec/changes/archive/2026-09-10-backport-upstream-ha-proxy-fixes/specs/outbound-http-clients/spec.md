## MODIFIED Requirements

### Requirement: Client-to-LB routing hints remain hop-local

The service MUST discard inbound `x-codex-routing-hint` values case-insensitively. Proxy-routed subscription Responses requests with a known model MUST synthesize a new hint from the final normalized model and requested service tier when opening an HTTP request or WebSocket handshake. Inbound hint values MUST NOT determine the synthesized hint. Inbound LB API-key authentication MUST NOT disable synthesis for a selected subscription account. Non-subscription transports MUST NOT synthesize a Codex-backend hint.

#### Scenario: HTTP egress omits routing hint

- **WHEN** an inbound HTTP request includes `x-codex-routing-hint` and the builder has no trusted subscription routing context
- **THEN** the outbound request omits that header

#### Scenario: WebSocket egress omits routing hint

- **WHEN** an inbound WebSocket handshake includes any case spelling of `x-codex-routing-hint` and no trusted subscription routing context is available
- **THEN** the outbound handshake omits that header

#### Scenario: HTTP egress replaces an untrusted routing hint

- **WHEN** an inbound subscription request advertises a different model or tier in its hint
- **THEN** the HTTP egress hint reflects the final normalized outbound model and tier only

#### Scenario: WebSocket egress discards inbound routing hints

- **WHEN** an inbound handshake includes any case spelling of `x-codex-routing-hint`
- **THEN** that value is not forwarded
- **AND** any synthesized handshake hint uses trusted request state

## ADDED Requirements

### Requirement: Enabled model schedulers warm client identity on every replica

Every replica with model refresh enabled MUST refresh its process-local Codex client-version cache independently of model-refresh leadership. Outbound non-native fingerprints MUST read the warmed cache without network I/O on the request path. Lookup failure MUST preserve cached or configured fallback behavior and MUST NOT prevent the subsequent model refresh or follower reconciliation. Scheduler shutdown MUST cancel and await a pending warmup. The unconfigured fallback MUST be at least 0.153.4; explicit operator overrides MUST remain respected.

#### Scenario: Follower forwards the warmed version

- **WHEN** a follower refresh tick discovers client version 9.9.9
- **THEN** a subsequent non-native outbound request uses 9.9.9 in User-Agent and version
- **AND** the follower does not perform leader-only account model discovery

#### Scenario: Lookup failure does not stop reconciliation

- **WHEN** a replica's client-version lookup fails
- **THEN** its model refresh or follower reconciliation still runs
- **AND** outbound requests retain the cached or configured fallback version

#### Scenario: Shutdown cancels pending warmup

- **WHEN** the scheduler stops while its version lookup is pending
- **THEN** the lookup task is cancelled and awaited without a detached background task

### Requirement: Subscription transports synthesize final request routing hints

Subscription Responses HTTP requests, internal and persistent WebSocket handshakes, HTTP fallback, bridge reconnects, and client-request compaction MUST synthesize `x-codex-routing-hint: model=<model>;tier=<tier>` from final normalized request state. An absent tier MUST produce only `model=<model>`. A preconnect without a model MUST omit the hint. Reusing a socket MUST NOT reconnect solely to replace its handshake hint. Hint synthesis MUST NOT change payloads, entitlements, usage, retries, or actual response tiers, and MUST NOT depend on the optional ChatGPT account-ID header being present.

#### Scenario: Fast aliases and tier prohibition agree with the body

- **WHEN** a subscription Responses or compact request normalizes Fast to priority or removes it under policy
- **THEN** its hint carries exactly the same resulting model and tier as the outbound body

#### Scenario: Ultrafast and actual response tier remain distinct

- **WHEN** an eligible subscription request selects ultrafast but the actual response reports another tier
- **THEN** the request hint carries ultrafast
- **AND** response and usage bookkeeping preserve the actual response tier

#### Scenario: Reconnect and HTTP fallback retain request tier

- **WHEN** a subscription bridge reconnects or a WebSocket attempt falls back to HTTP
- **THEN** the new handshake or HTTP request hint uses the final requested model and tier

#### Scenario: Non-subscription transport omits the hint

- **WHEN** a custom model source or low-level transport without subscription provenance is invoked
- **THEN** no inbound or synthesized Codex-backend routing hint is emitted
