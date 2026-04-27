## ADDED Requirements

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
