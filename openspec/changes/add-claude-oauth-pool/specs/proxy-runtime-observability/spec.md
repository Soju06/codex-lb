# proxy-runtime-observability Specification (delta)

## ADDED Requirements

### Requirement: Claude metrics namespace

When `CODEX_LB_METRICS_ENABLED=true` and `prometheus-client` is installed, the system SHALL expose the following Prometheus metrics:

- `codex_lb_claude_requests_total` (counter, labels: `status` where `status` ∈ `success` | `rate_limited` | `upstream_error` | `auth_error`)
- `codex_lb_claude_refresh_total` (counter, labels: `result` where `result` ∈ `success` | `invalid_grant` | `error`)
- `codex_lb_claude_accounts_active` (gauge)

#### Scenario: Successful Claude request increments success counter

- **WHEN** a Claude proxy request completes with status 200
- **THEN** `codex_lb_claude_requests_total{status="success"}` is incremented by 1

#### Scenario: 429 from Anthropic increments rate_limited counter

- **WHEN** a Claude proxy request receives a 429 from upstream
- **THEN** `codex_lb_claude_requests_total{status="rate_limited"}` is incremented by 1

#### Scenario: Successful OAuth refresh increments refresh success counter

- **WHEN** the auth guardian scheduler refreshes a Claude access token successfully
- **THEN** `codex_lb_claude_refresh_total{result="success"}` is incremented by 1

#### Scenario: invalid_grant refresh increments refresh error counter

- **WHEN** the auth guardian scheduler attempts a refresh and Anthropic returns `invalid_grant`
- **THEN** `codex_lb_claude_refresh_total{result="invalid_grant"}` is incremented by 1

#### Scenario: Active account gauge reflects the pool size

- **WHEN** the dashboard reads `/metrics`
- **THEN** `codex_lb_claude_accounts_active` equals the count of `accounts` rows with `provider='claude'` and `is_active=true`

#### Scenario: Metrics disabled means no Claude metrics are exposed

- **GIVEN** `CODEX_LB_METRICS_ENABLED=false`
- **WHEN** a client scrapes `/metrics`
- **THEN** no `codex_lb_claude_*` metrics are present

### Requirement: Claude request log line shape

The system SHALL emit a structured log line on each Claude request completion with at minimum the fields: `request_id`, `account_id`, `model`, `input_tokens`, `output_tokens`, `anthropic_ratelimit_status`, and `status`. The log line SHALL be emitted exactly once per request (after streaming completes or after a non-streaming response).

#### Scenario: Successful non-streaming request logs usage once

- **WHEN** a non-streaming Claude request completes with 200 and `usage` in the body
- **THEN** exactly one log line with `request_id` and the `usage` totals is emitted

#### Scenario: Successful streaming request logs usage once at end

- **WHEN** a streaming Claude request receives the final `message_stop` event
- **THEN** exactly one log line with the `usage` totals from the final `message_delta` is emitted