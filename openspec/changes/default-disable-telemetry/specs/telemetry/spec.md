## MODIFIED Requirements

### Requirement: Consent state and default activation

Telemetry consent MUST be a persisted tri-state (`undecided`, `enabled`, `disabled`) defaulting to `undecided`, and while consent is `undecided` the service SHALL treat telemetry as inactive.

Upgrading an existing installation MUST introduce the consent state as `undecided`. A previously persisted `enabled` or `disabled` decision MUST remain effective after upgrade.

#### Scenario: Fresh install defaults to inactive

- **WHEN** codex-lb starts for the first time with no persisted consent and no environment override
- **THEN** consent is `undecided` and no telemetry network request is made

#### Scenario: Upgrade treats existing users as undecided and inactive

- **WHEN** an existing installation migrates to a version with this capability and has no persisted telemetry decision
- **THEN** the migrated consent state is `undecided`, telemetry remains inactive, and the one-time consent dialog is shown on next dashboard entry

#### Scenario: Existing explicit decision survives upgrade

- **WHEN** an installation with persisted `enabled` or `disabled` consent upgrades
- **THEN** the service MUST continue honoring that explicit decision

### Requirement: Startup notice while undecided

While consent is `undecided`, the elected leader MUST emit a single startup log line stating that anonymous telemetry is disabled by default and how to enable it. Non-leader replicas MUST NOT duplicate the notice.

#### Scenario: Headless operator is informed

- **WHEN** the service starts with consent `undecided`
- **THEN** exactly one log line states that telemetry is disabled by default and names `CODEX_LB_TELEMETRY_ENABLED=true` as the opt-in path
