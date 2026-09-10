## ADDED Requirements

### Requirement: Upstream connect timeout is dashboard-managed

The upstream connect timeout applied to every outbound upstream request — Responses streams, thread-goal and control calls, compaction, transcription, file uploads, upstream WebSocket handshakes and the HTTP bridge owner forward — MUST be the effective `upstream_connect_timeout_seconds` resolved as code default < environment < dashboard: a non-NULL `dashboard_settings.upstream_connect_timeout_seconds` overrides `CODEX_LB_UPSTREAM_CONNECT_TIMEOUT_SECONDS`. Consumers MUST read it from the `SettingsCache` snapshot bound at the request or connection entry point (never from the database on the request path); per-attempt overrides that clamp the connect timeout to a remaining budget keep applying on top of the effective value. `PUT /api/settings` MUST reject, with `400 timeout_invariant_violation`, a connect timeout that would exceed the effective proxy, compact or transcription request budget, because such a value is clamped to the budget and can never be honoured.

#### Scenario: Dashboard connect timeout overrides startup environment

- **GIVEN** `CODEX_LB_UPSTREAM_CONNECT_TIMEOUT_SECONDS=8` and an operator stores `3` through `PUT /api/settings`
- **WHEN** a new request opens an upstream connection on any replica
- **THEN** the aiohttp connect (`sock_connect`) timeout is 3 seconds
- **AND** `GET /api/settings` reports `upstreamConnectTimeoutSeconds: 3` with `provenance.upstream_connect_timeout_seconds.source = "dashboard"`

#### Scenario: Connect timeout above a budget is rejected

- **GIVEN** the effective transcription request budget is 120 seconds
- **WHEN** the operator sends `PUT /api/settings` with `upstreamConnectTimeoutSeconds: 130`
- **THEN** the request is rejected with `400` and code `timeout_invariant_violation` naming `upstream-connect-within-transcription-budget`
- **AND** the same `PUT` with `transcriptionRequestBudgetSeconds: 150` alongside is accepted

#### Scenario: Startup warns when the environment is shadowed

- **GIVEN** `CODEX_LB_UPSTREAM_CONNECT_TIMEOUT_SECONDS` is set in the environment and the dashboard column is non-NULL
- **WHEN** the process starts
- **THEN** one WARN names the shadowed variable and points at the dashboard
- **AND** no WARN is logged when the variable is unset or the column is NULL
