# proxy-runtime-observability Specification

## Purpose

Define proxy observability contracts so runtime failures, routing decisions, and admission rejections remain diagnosable.
## Requirements
### Requirement: Proxy 4xx/5xx responses are logged with error detail
When the proxy returns a 4xx or 5xx response for a proxied request, the system MUST log the request id, method, path, status code, error code, and error message to the console. For local admission rejections, the log MUST also include the rejection stage or lane.

#### Scenario: Local admission rejection is logged
- **WHEN** the proxy rejects a request locally because a downstream or expensive-work admission lane is full
- **THEN** the console log includes the local response status, normalized error code and message
- **AND** it includes which admission lane or stage rejected the request

### Requirement: Optional upstream payload tracing
When request-shape tracing for proxy routing is enabled, the system MUST log affinity decision metadata without exposing full prompt text or full cache keys. The trace MUST include request id, request kind, sticky kind, sticky-key source, whether a session header was present, whether a prompt-cache key was set/injected, and a stable tools hash when tools are present.

#### Scenario: Affinity request-shape tracing is enabled
- **WHEN** the proxy resolves routing for a Responses or compact request while request-shape tracing is enabled
- **THEN** the console shows the chosen sticky kind, sticky-key source, prompt-cache-key presence/injection state, and tools hash
- **AND** the console does not log raw prompt text or the full prompt-cache key unless the explicit raw-key flag is enabled

### Requirement: Upstream 403 diagnostics include account-scoped recovery context

When proxy request handling or background model-registry refresh receives an upstream `403` that influences account selection or failover, the console log MUST include enough context to explain whether the failure was retried, cooled down, or surfaced.

#### Scenario: Websocket connect 403 log explains failover and cooldown

- **WHEN** a pre-commit upstream websocket connect fails with HTTP `403`
- **THEN** the console log includes the request id, transport, account id, model when known, HTTP status, normalized error code, upstream request identifier when present, and the chosen action (`failover_next` or `surface`)
- **AND** when a cooldown is applied, the log includes the cooldown duration

#### Scenario: Model refresh 403 log explains attribution

- **WHEN** background model-registry refresh receives HTTP `403`
- **THEN** the console log includes the account id, plan, HTTP status, upstream request identifier when present, and a bounded upstream response preview

### Requirement: Metrics expose recovery decisions for pre-commit failures

When Prometheus metrics are enabled, the service MUST expose counters for failover decisions, drain transitions, and client-exposed pre-commit upstream errors so operators can measure whether recovery paths are working.

#### Scenario: Pre-commit failover increments failover counter

- **WHEN** the proxy retries another eligible account before any downstream bytes are emitted
- **THEN** the failover counter increments with the transport and failure class labels for that decision

#### Scenario: Client-visible pre-commit error increments exposed-error counter

- **WHEN** the proxy must surface a pre-commit upstream failure because no more eligible accounts remain
- **THEN** the client-exposed error counter increments with the transport and normalized error code labels for that surfaced failure

