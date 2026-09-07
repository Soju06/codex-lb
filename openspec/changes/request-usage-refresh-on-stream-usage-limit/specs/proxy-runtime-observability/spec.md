# proxy-runtime-observability Delta

## ADDED Requirements

### Requirement: Upstream reasoning-replay rejections are counted

When Prometheus support is available the proxy MUST expose a label-free counter
named `codex_lb_upstream_reasoning_replay_400_total` and MUST increment it once
per upstream stream failure that is an HTTP 400 rejection, or an
`invalid_request_error` frame without an HTTP status, whose error message
references reasoning. Counting MUST NOT alter failure classification, account
health, or failover, MUST NOT log the rejection message body, and MUST degrade to
a no-op when the Prometheus client is absent.

#### Scenario: Reasoning replay rejection is counted

- **WHEN** upstream rejects a stream with HTTP 400 and a message such as `Item with id 'rs_...' of type 'reasoning' was provided without its required following item.`
- **THEN** `codex_lb_upstream_reasoning_replay_400_total` increments by one
- **AND** the failure is classified and penalized exactly as before

#### Scenario: Other rejections are not counted

- **WHEN** upstream rejects a stream with HTTP 400 without referencing reasoning, or with a non-400 status whose message mentions reasoning
- **THEN** the counter does not change

#### Scenario: Missing Prometheus client

- **WHEN** the Prometheus client is not installed
- **THEN** counting is a no-op and stream error handling is unchanged
