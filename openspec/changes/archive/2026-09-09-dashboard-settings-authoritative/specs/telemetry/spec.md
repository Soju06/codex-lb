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

### Requirement: Dashboard opt-out notification

The service MUST send one final signed `POST /v1/optout` notification for each dashboard-driven effective consent transition from active to inactive, and MUST complete any required instance registration and activation before sending that notification. The notification MUST use the telemetry instance identity and snapshot signing scheme, MUST be isolated from the settings API response, and MUST NOT be sent when the environment alone makes telemetry inactive (no dashboard decision persisted). Persisting a dashboard decision ends environment control: a persisted decision that flips the effective state from active to inactive MUST send exactly one notice even while `CODEX_LB_TELEMETRY_ENABLED` is set.

#### Scenario: Opt-out fires exactly once per transition

- **WHEN** dashboard consent transitions from undecided or enabled active telemetry to disabled
  inactive telemetry
- **THEN** exactly one opt-out notification is attempted for that transition before telemetry
  becomes silent

#### Scenario: Environment opt-out stays silent

- **WHEN** `CODEX_LB_TELEMETRY_ENABLED=false` makes telemetry inactive while no dashboard decision
  is persisted
- **THEN** no opt-out notification or other telemetry network request is attempted

#### Scenario: Dashboard decision ends environment control

- **GIVEN** `CODEX_LB_TELEMETRY_ENABLED=true` keeps telemetry active while no decision is persisted
- **WHEN** the operator disables telemetry in settings
- **THEN** exactly one opt-out notification is attempted and telemetry becomes silent
- **AND** enabling telemetry in settings while `CODEX_LB_TELEMETRY_ENABLED=false` kept it inactive
  attempts no opt-out notification because the transition is inactive to active

#### Scenario: Opt-out failure is isolated

- **WHEN** registration, activation, or opt-out transmission fails
- **THEN** the failure uses a total timeout of no more than five seconds, retries no more than
  once, is logged only at debug level, does not raise to the caller, and does not delay or alter
  the successful settings API response

#### Scenario: A later transition may notify again

- **WHEN** an operator re-enables telemetry and later disables it again through the dashboard
- **THEN** the later active-to-inactive transition attempts exactly one new opt-out notification
