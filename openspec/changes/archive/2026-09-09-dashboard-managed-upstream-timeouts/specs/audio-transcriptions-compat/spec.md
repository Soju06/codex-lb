## MODIFIED Requirements

### Requirement: Transcription proxy requests use a bounded retry budget
The system MUST enforce a configurable total request budget for transcription proxy routes across account selection, token refresh, upstream connect, and upstream response handling. Once that budget is exhausted, the proxy MUST stop retrying and return a stable OpenAI-format timeout failure instead of waiting through repeated hard-coded timeout windows. The budget is `transcription_request_budget_seconds`, dashboard-managed (`configuration-tiers`): a non-NULL `dashboard_settings.transcription_request_budget_seconds` MUST override `CODEX_LB_TRANSCRIPTION_REQUEST_BUDGET_SECONDS`, which remains a deprecated fallback, and the effective value MUST come from the `SettingsCache` snapshot bound to the request.

#### Scenario: Transcription budget expires before retry
- **WHEN** a transcription request consumes its configured request budget before a retry attempt can begin
- **THEN** the service returns `502` with OpenAI-format error code `upstream_unavailable`
- **AND** no further upstream attempt starts

#### Scenario: 401 transcription retry respects remaining budget
- **WHEN** the first transcription attempt returns 401 and token refresh succeeds while request budget remains
- **THEN** the retry uses the refreshed account metadata
- **AND** the retry only proceeds if enough request budget remains for another attempt

#### Scenario: Dashboard budget overrides startup environment
- **GIVEN** `CODEX_LB_TRANSCRIPTION_REQUEST_BUDGET_SECONDS=120` and an operator stores `240` through `PUT /api/settings`
- **WHEN** a new transcription request computes its deadline on any replica
- **THEN** the deadline uses the 240 second dashboard value
- **AND** `GET /api/settings` reports `provenance.transcription_request_budget_seconds.source = "dashboard"`
