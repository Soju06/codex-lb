## ADDED Requirements

### Requirement: Automation scheduling can be paused from the dashboard

The dashboard setting `automations_scheduler_enabled` (a nullable `dashboard_settings` column; NULL inherits the deprecated `CODEX_LB_AUTOMATIONS_SCHEDULER_ENABLED` environment variable, then the default `true`) SHALL decide whether automations run. It SHALL be exposed with provenance on `GET`/`PUT /api/settings` and offered both in Settings → Advanced → Background jobs and as a "pause all automations" control on the Automations page. The scheduler loop SHALL always start; each tick SHALL read the effective value from the dashboard-settings snapshot at the start of the tick and, while it is `false`, SHALL dispatch nothing — neither scheduled cycles nor pending manual runs — so a change applies on the next tick on every replica without a restart. While paused, `POST /api/automations/{id}/run-now` SHALL be refused with a `409` conflict error whose code is `automations_paused` and SHALL create no run. The scheduler MUST NOT read the database inside its lock to obtain the value: the snapshot is taken once per tick before the leader-gated body and passed into it. A failure of that read MUST NOT terminate the loop; the tick is logged and the next one retries.

#### Scenario: Dashboard pause skips the next tick

- **GIVEN** the scheduler was started with `automations_scheduler_enabled` effectively `true`
- **WHEN** an operator sets `automations_scheduler_enabled` to `false` in the dashboard
- **THEN** the next scheduler tick dispatches no scheduled cycle and no manual run
- **AND** no replica was restarted

#### Scenario: Resume applies on the next tick

- **GIVEN** automations are paused from the dashboard
- **WHEN** the operator sets `automations_scheduler_enabled` back to `true` (or clears it so the inherited `true` applies)
- **THEN** the next scheduler tick dispatches due work again

#### Scenario: Run now is refused while paused

- **GIVEN** automations are paused from the dashboard
- **WHEN** a dashboard user calls `POST /api/automations/{id}/run-now`
- **THEN** the response is `409` with error code `automations_paused`
- **AND** no automation run is created for that job

#### Scenario: Environment alias applies only while the dashboard value is unset

- **GIVEN** `CODEX_LB_AUTOMATIONS_SCHEDULER_ENABLED=false` and no dashboard value
- **WHEN** an operator sets `automations_scheduler_enabled` to `true` in the dashboard
- **THEN** ticks dispatch due work and `provenance.automations_scheduler_enabled.source` is `dashboard`
