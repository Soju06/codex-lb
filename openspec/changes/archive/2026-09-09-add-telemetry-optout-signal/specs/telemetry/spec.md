## ADDED Requirements

### Requirement: Snapshot payload declares active consent

Every snapshot payload MUST include a top-level `consent` field whose value is the resolved
persisted consent state `undecided` or `enabled`; `disabled` MUST NOT appear because snapshots
are not transmitted while telemetry is inactive.

#### Scenario: Undecided snapshot declares consent

- **WHEN** telemetry is active under the default undecided consent state
- **THEN** the transmitted snapshot and exact payload preview contain `consent: "undecided"`

#### Scenario: Enabled snapshot declares consent

- **WHEN** telemetry is active under persisted enabled consent
- **THEN** the transmitted snapshot and exact payload preview contain `consent: "enabled"`

### Requirement: Dashboard opt-out notification

The service MUST send one final signed `POST /v1/optout` notification for each
dashboard-driven effective consent transition from active to inactive, and MUST complete any
required instance registration and activation before sending that notification. The notification
MUST use the telemetry instance identity and snapshot signing scheme, MUST be isolated from the
settings API response, and MUST NOT be sent for an environment-controlled consent path.

#### Scenario: Opt-out fires exactly once per transition

- **WHEN** dashboard consent transitions from undecided or enabled active telemetry to disabled
  inactive telemetry without an environment override
- **THEN** exactly one opt-out notification is attempted for that transition before telemetry
  becomes silent

#### Scenario: Environment kill switch stays silent

- **WHEN** `CODEX_LB_TELEMETRY_ENABLED=false` makes telemetry inactive or a dashboard decision
  is persisted while consent is controlled by either environment override value
- **THEN** no opt-out notification or other telemetry network request is attempted

#### Scenario: Opt-out failure is isolated

- **WHEN** registration, activation, or opt-out transmission fails
- **THEN** the failure uses a total timeout of no more than five seconds, retries no more than
  once, is logged only at debug level, does not raise to the caller, and does not delay or alter
  the successful settings API response

#### Scenario: A later transition may notify again

- **WHEN** an operator re-enables telemetry and later disables it again through the dashboard
  without an environment override
- **THEN** the later active-to-inactive transition attempts exactly one new opt-out notification

## MODIFIED Requirements

### Requirement: Disabled means zero telemetry traffic

Except for the single decision-time opt-out notification on a dashboard-driven active-to-inactive
transition, when resolved consent is `disabled` the service MUST NOT open any network connection
to the telemetry endpoint. The environment kill-switch path MUST NOT receive this exception and
MUST remain completely silent.

#### Scenario: No connection attempts when disabled

- **WHEN** telemetry is disabled and the service runs through startup and a 24-hour scheduler
  cycle outside the dashboard decision-time transition
- **THEN** no connection attempt to the telemetry endpoint is made
