# telemetry

## MODIFIED Requirements

### Requirement: Settings toggle and environment kill switch

The dashboard settings MUST expose a telemetry toggle reflecting the resolved consent state. Consent MUST resolve as `persisted decision > CODEX_LB_TELEMETRY_ENABLED > default`: a persisted dashboard decision is authoritative and MUST NOT be overridden by the environment variable. `CODEX_LB_TELEMETRY_ENABLED` MUST apply only while the persisted state is `undecided` (`false` disables all transmission, `true` enables and suppresses the consent dialog) and MUST be reported with `source: env`. The toggle MUST remain usable while the environment value is in effect so the operator can persist a decision, and the dashboard MUST show a notice that the environment value is the current fallback.

#### Scenario: Headless deployment disables via environment

- **WHEN** the service runs with `CODEX_LB_TELEMETRY_ENABLED=false` and no decision is persisted
- **THEN** no telemetry network traffic occurs, the consent API reports `source: env`, and the
  settings toggle shows telemetry as disabled with the environment-fallback notice

#### Scenario: Persisted decision wins over the environment

- **GIVEN** the service runs with `CODEX_LB_TELEMETRY_ENABLED=true`
- **WHEN** the operator disables telemetry in settings
- **THEN** consent persists as `disabled`, the response reports `source: persisted` and `active: false`
- **AND** subsequent reads keep reporting the persisted decision while the variable stays set
- **AND** transmission stops without restart

#### Scenario: Toggle flips persisted consent

- **WHEN** the operator disables telemetry in settings without an environment value set
- **THEN** consent persists as `disabled` and transmission stops without restart
