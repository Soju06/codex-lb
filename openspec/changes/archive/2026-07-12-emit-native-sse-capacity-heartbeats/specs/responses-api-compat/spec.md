## ADDED Requirements

### Requirement: Direct capacity-wait progress follows the downstream stream contract

When direct HTTP/SSE streaming waits for recoverable local account capacity, the proxy MUST emit `codex.keepalive` progress events if the OpenAI SDK stream contract is disabled, regardless of whether the route propagates HTTP errors.
The proxy MUST continue suppressing those non-standard progress events before
startup when both HTTP error propagation and the OpenAI SDK stream contract are
enabled.

#### Scenario: Native image-capable bypass emits capacity progress

- **GIVEN** an image-capable native Codex request bypasses the HTTP responses bridge
- **AND** the route propagates HTTP errors with `enforce_openai_sdk_contract = false`
- **WHEN** direct account selection waits for `account_stream_cap` or `account_response_create_cap` to recover
- **THEN** the stream emits `codex.keepalive` with `status = "waiting_for_account_capacity"` before capacity is released
- **AND** no upstream response attempt or terminal event occurs before capacity is released
- **AND** account selection retries and the real upstream completion is forwarded after capacity becomes available

#### Scenario: OpenAI SDK startup error remains structured

- **GIVEN** a route propagates HTTP errors with `enforce_openai_sdk_contract = true`
- **WHEN** a local account-capacity wait occurs before stream startup
- **THEN** the proxy MUST NOT emit `codex.keepalive` before startup
- **AND** a terminal local-cap failure remains available to the route's structured HTTP error path
