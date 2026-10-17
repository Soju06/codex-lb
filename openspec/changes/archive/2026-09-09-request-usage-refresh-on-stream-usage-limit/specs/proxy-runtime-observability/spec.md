# proxy-runtime-observability Delta

## ADDED Requirements

### Requirement: Upstream reasoning-replay rejections are counted

When Prometheus support is available the proxy MUST expose a label-free counter
named `codex_lb_upstream_reasoning_replay_400_total` and MUST increment it exactly
once per upstream stream failure that is an HTTP 400 rejection, or a terminal
`error` / `response.failed` frame carrying `invalid_request_error` without an HTTP
status, whose error message references reasoning. Frames MUST be counted where
the terminal frame is classified -- on the SSE streaming path, the websocket
path, and the HTTP bridge (which finalizes through the websocket path) --
independent of whether an account-health write follows, because
`invalid_request_error` is never penalized and therefore never reaches the
account-health handler. Counting MUST NOT alter failure classification, account
health, or failover, MUST NOT log the rejection message body, and MUST degrade to
a no-op when the Prometheus client is absent.

#### Scenario: Reasoning replay rejection is counted

- **WHEN** upstream rejects a stream with HTTP 400 and a message such as `Item with id 'rs_...' of type 'reasoning' was provided without its required following item.`
- **THEN** `codex_lb_upstream_reasoning_replay_400_total` increments by one
- **AND** the failure is classified and penalized exactly as before

#### Scenario: Terminal frames are counted without an account-health write

- **WHEN** an upstream SSE stream, websocket session, or HTTP-bridge session ends with a terminal `error` or `response.failed` frame whose code is `invalid_request_error` and whose message references reasoning
- **THEN** `codex_lb_upstream_reasoning_replay_400_total` increments by exactly one
- **AND** the frame is neither penalized nor otherwise classified differently than before

#### Scenario: Other rejections are not counted

- **WHEN** upstream rejects a stream with HTTP 400 without referencing reasoning, with a non-400 status whose message mentions reasoning, or with a terminal frame whose code is not `invalid_request_error`
- **THEN** the counter does not change

#### Scenario: Missing Prometheus client

- **WHEN** the Prometheus client is not installed
- **THEN** counting is a no-op and stream error handling is unchanged
