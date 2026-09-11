## MODIFIED Requirements

### Requirement: Reset credit polling can be disabled

The dashboard setting `rate_limit_reset_credits_refresh_enabled` (a nullable `dashboard_settings` column; NULL inherits the deprecated `CODEX_LB_RATE_LIMIT_RESET_CREDITS_REFRESH_ENABLED` environment variable, then the default `true`) SHALL enable or disable background reset-credit polling. It SHALL be exposed with provenance on `GET`/`PUT /api/settings` (Settings → Advanced → Background jobs). The polling loop SHALL always start; each refresh cycle SHALL read the effective value from the dashboard-settings snapshot before taking its lock and SHALL skip the cycle (no upstream fetch, no automatic redemption) while it is `false`, so a change applies on the next cycle on every replica without a restart. Because the refresh loop is the sole driver of automatic reset-credit redemption, disabling polling SHALL also disable automatic redemption; when polling is effectively disabled while the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is enabled, the system SHALL log a configuration-conflict warning at startup naming both settings. The dashboard settings update SHALL reject, with a bad-request error naming the polling toggle, any request that would newly produce the unrunnable pair "auto-redeem enabled, polling effectively disabled" — both the request that newly enables `auto_redeem_reset_credits_before_expiry` and the request that disables the polling toggle while the opt-in stays enabled. The gate SHALL evaluate the proposed effective values (a value in the request, else the inherited value when the request clears it, else the current effective value). Setting both consistently in one request (both on, or both off) SHALL succeed, and a payload that only re-saves an already inconsistent pair SHALL remain accepted so unrelated settings edits are not blocked.

#### Scenario: Operator disables background polling

- **GIVEN** the polling loop was started with `rate_limit_reset_credits_refresh_enabled` effectively `true`
- **WHEN** an operator sets `rate_limit_reset_credits_refresh_enabled` to `false` in the dashboard
- **THEN** the next refresh cycle performs no upstream reset-credits fetch and no automatic redemption
- **AND** setting it back to `true` (or clearing it so the inherited `true` applies) makes the following cycle fetch again
- **AND** no replica was restarted

#### Scenario: Disabled polling conflicts with persisted auto-redeem opt-in

- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is effectively `false` (dashboard value, or environment alias while the dashboard value is unset)
- **AND** the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is `true`
- **WHEN** the application starts
- **THEN** the system logs a configuration-conflict warning naming both settings
- **AND** no automatic reset-credit redemption occurs while polling remains disabled

#### Scenario: Auto-redeem opt-in is rejected while polling is disabled

- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is effectively `false`
- **AND** the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is `false`
- **WHEN** a dashboard settings update sets `auto_redeem_reset_credits_before_expiry` to `true` without also enabling the polling toggle
- **THEN** the update is rejected with a bad-request error naming the polling toggle
- **AND** the persisted setting remains `false`

#### Scenario: Disabling polling is rejected while auto-redeem is enabled

- **GIVEN** the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is `true`
- **AND** `rate_limit_reset_credits_refresh_enabled` is effectively `true`
- **WHEN** a dashboard settings update sets `rate_limit_reset_credits_refresh_enabled` to `false` without also turning the opt-in off
- **THEN** the update is rejected with the same bad-request error naming the polling toggle
- **AND** the polling toggle remains effectively `true`
- **AND** turning both off in one request succeeds

#### Scenario: Enabling polling and auto-redeem in one request succeeds

- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is `false` in the dashboard
- **WHEN** a dashboard settings update sets both `auto_redeem_reset_credits_before_expiry` and `rate_limit_reset_credits_refresh_enabled` to `true`
- **THEN** the update succeeds and both values are persisted

#### Scenario: Persisted auto-redeem does not block unrelated settings edits

- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is effectively `false`
- **AND** the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is already `true`
- **WHEN** a full settings payload that keeps the opt-in unchanged is submitted
- **THEN** the update succeeds

#### Scenario: Environment alias applies only while the dashboard value is unset

- **GIVEN** `CODEX_LB_RATE_LIMIT_RESET_CREDITS_REFRESH_ENABLED=false` and no dashboard value
- **WHEN** an operator sets `rate_limit_reset_credits_refresh_enabled` to `true` in the dashboard
- **THEN** refresh cycles fetch again and `provenance.rate_limit_reset_credits_refresh_enabled.source` is `dashboard`
