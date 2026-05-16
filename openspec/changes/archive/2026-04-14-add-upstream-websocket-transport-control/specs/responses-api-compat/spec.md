## ADDED Requirements
### Requirement: Streaming Responses transport is operator-selectable
For streaming Codex/Responses proxy requests, the system MUST let operators choose the upstream transport strategy through dashboard settings. The resolved strategy MAY be `auto`, `http`, or `websocket`, and `default` MUST defer to the server configuration default.

#### Scenario: Dashboard forces websocket upstream transport
- **WHEN** the dashboard setting `upstream_stream_transport` is set to `"websocket"`
- **THEN** streaming upstream `/backend-api/codex/responses` traffic MUST use the native Responses WebSocket transport
- **AND** the proxy MUST continue bridging the upstream stream back through the existing client-facing Responses interface

#### Scenario: Dashboard forces HTTP upstream transport
- **WHEN** the dashboard setting `upstream_stream_transport` is set to `"http"`
- **THEN** streaming upstream `/backend-api/codex/responses` traffic MUST use the existing HTTP Responses transport

#### Scenario: Auto transport falls back when websocket upgrades are rejected
- **WHEN** the resolved upstream transport strategy is `"auto"`
- **AND** the proxy initially selects the native Responses WebSocket transport
- **AND** the upstream rejects the websocket upgrade with HTTP `403`, `404`, or `426`
- **THEN** the proxy MUST retry the same streaming request over the existing HTTP Responses transport before failing the client stream

#### Scenario: Session affinity alone does not trigger websocket upstream transport
- **WHEN** a backend Codex Responses request includes `session_id` only for routing affinity
- **AND** it does not include an allowlisted native Codex `originator` or explicit Codex websocket feature headers
- **THEN** the `"auto"` transport strategy MUST keep using the existing HTTP Responses transport unless model preference selects websocket

#### Scenario: Auto transport honors websocket-preferred bootstrap models before registry warmup
- **WHEN** the upstream transport strategy is `"auto"`
- **AND** the model registry snapshot has not loaded yet
- **AND** the request targets a locally bootstrapped websocket-preferred model family such as `gpt-5.4` or `gpt-5.4-*`
- **THEN** the proxy MUST still select the native Responses WebSocket transport

#### Scenario: Legacy settings objects keep the historical HTTP default
- **WHEN** transport selection runs against a legacy settings object that does not expose the newer upstream transport fields
- **THEN** the proxy MUST preserve the pre-feature HTTP transport default for model-preference auto-selection unless an explicit legacy websocket mode or native Codex websocket signal opts in

#### Scenario: Websocket upstream handshake strips hop-by-hop inbound headers
- **WHEN** streaming upstream traffic uses the native Responses WebSocket transport
- **THEN** the upstream websocket handshake MUST omit hop-by-hop request headers such as `Connection`, `Keep-Alive`, `Transfer-Encoding`, and `Upgrade`
- **AND** the proxy MUST also omit any additional header names named by the inbound `Connection` header before calling the upstream websocket client

#### Scenario: Websocket response.create payload omits HTTP-only transport fields
- **WHEN** streaming upstream traffic uses the native Responses WebSocket transport
- **THEN** the proxy MUST omit HTTP-only request fields such as `stream` and `background` from the upstream `response.create` payload
- **AND** the proxy MUST still preserve non-transport request fields when building that websocket payload

### Requirement: Fast service tier aliases canonical priority locally and upstream
When a Responses request includes `service_tier: "fast"`, the service MUST canonicalize that alias to `service_tier: "priority"` for local billable state and outbound upstream payloads.

#### Scenario: Fast mode request remains locally visible
- **WHEN** a client sends a valid Responses request with `service_tier: "fast"`
- **THEN** the proxy accepts the request
- **AND** the local request state uses the canonical value `"priority"`
- **AND** the outbound upstream request uses `service_tier: "priority"`
- **AND** operators can still compare requested-versus-actual tier behavior without persisting the raw alias into billable request logs

### Requirement: Streaming request logs preserve the billable service tier
When a streaming Responses request completes, the persisted request log MUST keep the effective service tier used for pricing and summaries, while requested-versus-actual tier comparison remains an observability concern outside the billable `service_tier` field.

#### Scenario: Upstream downgrades the reported tier
- **WHEN** a client sends `service_tier: "priority"` for a streaming Responses request
- **AND** the upstream response later reports `service_tier: "auto"` or `"default"`
- **THEN** the persisted request log entry records the upstream-reported effective `service_tier`
