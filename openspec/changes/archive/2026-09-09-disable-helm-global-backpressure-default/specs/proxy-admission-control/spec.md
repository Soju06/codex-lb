## ADDED Requirements

### Requirement: Helm default leaves global backpressure disabled

The Helm chart MUST default `config.backpressureMaxConcurrentRequests` to
`0` so a default install sets
`CODEX_LB_BACKPRESSURE_MAX_CONCURRENT_REQUESTS` to `"0"`. A value of `0`
MUST leave the process-wide backpressure semaphore uninstalled. A positive
operator override MUST still render into that ConfigMap key. The default
MUST NOT install one global concurrent-request cap across proxy HTTP,
websocket, compact, and dashboard traffic.

#### Scenario: Default Helm ConfigMap disables global backpressure

- **WHEN** the chart is rendered with default values
- **THEN** the ConfigMap `CODEX_LB_BACKPRESSURE_MAX_CONCURRENT_REQUESTS`
  value is `"0"`

#### Scenario: Explicit Helm override renders the global cap

- **WHEN** an operator sets `config.backpressureMaxConcurrentRequests=37`
- **THEN** the ConfigMap `CODEX_LB_BACKPRESSURE_MAX_CONCURRENT_REQUESTS`
  value is `"37"`
