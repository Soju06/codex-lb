## ADDED Requirements

### Requirement: Bridge-ring heartbeat health and periodic phase outcomes are observable
The service MUST expose the probed replica's nonnegative `heartbeat_age_seconds` in the `/health/ready` `bridge_ring` payload when its ring row exists, and MUST expose a null value when no local row exists or its age cannot be read. Prometheus-enabled runtimes MUST expose `codex_lb_bridge_ring_heartbeat_last_success_timestamp_seconds`, `codex_lb_bridge_ring_heartbeat_failures_total`, and `codex_lb_bridge_ring_maintenance_total` with only the bounded `phase` and `outcome` labels on the maintenance counter. The allowed phases MUST be `durable_ownership`, `idle_sweep`, and `cap_partition`; the allowed outcomes MUST be `success`, `failure`, and `timeout`. Heartbeat failures, recovery after failure, unexpected worker exits, phase failures, and phase timeouts MUST emit structured diagnostics with phase, outcome, elapsed or heartbeat-age seconds when available, and consecutive failure count when applicable. Metrics and logs MUST NOT contain request ids, raw affinity keys, API keys, account emails, request payloads, or instance ids as metric labels.

#### Scenario: Ready response exposes fresh local heartbeat age
- **GIVEN** the probed replica has a ring row with a fresh heartbeat
- **WHEN** `/health/ready` returns its bridge-ring payload
- **THEN** `heartbeat_age_seconds` is a nonnegative number below the stale threshold

#### Scenario: Missing local row exposes unknown age and fails readiness
- **GIVEN** bridge registration completed but the probed replica has no ring row
- **WHEN** `/health/ready` checks ring membership
- **THEN** the bridge-ring payload represents heartbeat age as null
- **AND** readiness fails because the local replica is not active

#### Scenario: Successful heartbeat updates the timestamp metric
- **WHEN** a ring heartbeat commits successfully
- **THEN** `codex_lb_bridge_ring_heartbeat_last_success_timestamp_seconds` advances to the successful attempt time
- **AND** no instance-id label is added

#### Scenario: Failed heartbeat is counted and recovery is logged
- **GIVEN** one or more heartbeat attempts failed or timed out
- **WHEN** the next heartbeat succeeds
- **THEN** the failed attempts have incremented `codex_lb_bridge_ring_heartbeat_failures_total`
- **AND** structured diagnostics record the failure sequence and subsequent recovery without sensitive identifiers

#### Scenario: Maintenance timeout is low-cardinality
- **WHEN** durable-ownership reconciliation exceeds its phase deadline
- **THEN** `codex_lb_bridge_ring_maintenance_total{phase="durable_ownership",outcome="timeout"}` increments
- **AND** the timeout log includes bounded elapsed-time data without request or affinity identifiers
