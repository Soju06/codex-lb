## ADDED Requirements

### Requirement: Account circuit breakers are constructed unconditionally and used per the dashboard toggle

The upstream client MUST create (and keep) a per-account circuit breaker regardless of the `circuit_breaker_enabled` toggle, and MUST decide per request whether to consult it — pre-call check, half-open probe, success and failure recording — from the effective `circuit_breaker_enabled` value of the dashboard-settings snapshot the request path resolved. Because the client is reached without a settings argument, every request path that reaches the client with an account (stream, compact, WebSocket connect, codex control, thread goal, transcription, warmup fan-out, and the background limit-warmup, quota-planner warmup and automation callers) MUST bind the resolved toggles to its task after taking its snapshot, MUST rebind them before every upstream attempt whose generator may have been handed to another task since (a streaming request that yielded a capacity keepalive before opening upstream), and the client MUST read them from the task; a task with no binding MUST fall back to the process environment value. Turning the toggle on or off in the dashboard MUST take effect on the next upstream attempt without a restart, and turning it off MUST NOT destroy or reset existing breaker state.

#### Scenario: Toggle turned off while a breaker is open

- **GIVEN** the dashboard toggle is on and repeated upstream server errors have opened an account's breaker
- **WHEN** an operator turns the circuit breaker off in the dashboard and the next request for that account is attempted
- **THEN** the attempt is not rejected by the open breaker
- **AND** the breaker object still exists and still reports open

#### Scenario: Toggle turned on without a restart

- **GIVEN** the process started with `CODEX_LB_CIRCUIT_BREAKER_ENABLED=false`
- **WHEN** an operator turns the circuit breaker on in the dashboard and an account fails with upstream server errors up to the fixed threshold
- **THEN** the account's breaker opens and the following attempt is rejected with the breaker-open error
