## ADDED Requirements

### Requirement: Responses concurrency pressure is observable

The service MUST expose low-cardinality logs and metrics for account-local in-flight create count, active stream count, leased token/cost pressure, cap rejections, lease stale reclaims, soft-affinity reroutes, and local-vs-upstream 429 classification. Observability MUST avoid raw prompt text, raw affinity keys, API keys, and request payload content.

#### Scenario: Local and upstream 429s are separated

- **WHEN** local admission rejects a request and upstream later returns a rate limit for another request
- **THEN** logs and metrics distinguish local overload reasons from normalized upstream `upstream_rate_limit`
- **AND** preserved upstream wire payloads may retain upstream codes such as `rate_limit_exceeded`, `usage_limit_reached`, or `insufficient_quota`

### Requirement: Streaming timeout diagnostics are emitted

For `/v1/responses` HTTP/SSE streams, the service MUST log low-cardinality diagnostics for early heartbeat emission, keepalive emission, startup wait timeout, downstream disconnect, and stream idle timeout. The diagnostics MUST include request id, route family, account id when known, timeout stage, and elapsed seconds where available, without exposing payload content or raw affinity keys.

#### Scenario: Keepalive path is diagnosable

- **WHEN** a streaming Responses request waits for upstream events long enough to emit keepalive data
- **THEN** the service records heartbeat or keepalive diagnostics
- **AND** the diagnostic does not include raw prompt-cache keys or request payloads
