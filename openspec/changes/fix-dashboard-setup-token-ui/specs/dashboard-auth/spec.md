## ADDED Requirements

### Requirement: Dashboard prompts for setup token and retries TOTP setup requests
The dashboard web client MUST support interactive setup token entry for TOTP setup flows. When setup requests are rejected with `dashboard_setup_forbidden`, the client MUST prompt for `CODEX_LB_DASHBOARD_SETUP_TOKEN`, then retry with `X-Codex-LB-Setup-Token`.

#### Scenario: Setup start forbidden response triggers token prompt and retry
- **WHEN** `POST /api/dashboard-auth/totp/setup/start` returns `403` with `dashboard_setup_forbidden`
- **THEN** the dashboard prompts for setup token and retries start request with `X-Codex-LB-Setup-Token`

#### Scenario: Setup confirm forbidden response triggers token prompt and retry
- **WHEN** `POST /api/dashboard-auth/totp/setup/confirm` returns `403` with `dashboard_setup_forbidden`
- **THEN** the dashboard prompts for setup token and retries confirm request with `X-Codex-LB-Setup-Token`
