## ADDED Requirements

### Requirement: Purged model-source pins are pruned regardless of retention opt-in

The retention pass SHALL delete `model_source_pins` rows whose `purge_at` is at or before the database clock, in `pin_key`-keyed batches of at most the retention batch size, one short transaction per batch under the SQLite writer section, and MUST report the count under `model_source_pins`. The purge MUST run on every leader-gated pass even when both retention windows are disabled (`0` or unset), because a purged pin answers no lookup any more; rows whose `purge_at` is still in the future MUST NOT be deleted. The hourly scheduler tick MUST therefore run the leader-gated pass regardless of the effective retention windows, while the request-log and usage-history pruning inside the pass stay gated by their windows. After the purge, while a drain deadline is armed, the pass MUST log a WARN `model_source_pins_drain_invariant_violated` when any remaining row has `purge_at >= drain_until`, and MUST NOT repair or delete such rows; a failing invariant check MUST NOT fail the pass.

#### Scenario: Purged pins are pruned while retention is disabled

- **GIVEN** neither retention window is configured
- **AND** one pin row whose `purge_at` passed 12 days ago, one tombstoned row whose `purge_at` is in the future, and one live row
- **WHEN** the retention pass runs
- **THEN** only the purged row is deleted and the pass reports `model_source_pins: 1`
- **AND** no `request_logs`, `usage_history` or `additional_usage_history` rows are deleted

#### Scenario: Backlog drains across batches

- **GIVEN** five purged pin rows and a batch size of two
- **WHEN** the pin purge runs
- **THEN** it deletes all five rows across three transactions and a second run deletes nothing

#### Scenario: Drain invariant alarm

- **GIVEN** an armed drain deadline and a pin row whose `purge_at` is not before it
- **WHEN** the retention pass runs
- **THEN** it logs `model_source_pins_drain_invariant_violated` and leaves the row in place
- **AND** rows written through the pin repository during the drain raise no alarm

#### Scenario: The tick never gates on the retention windows

- **GIVEN** both retention windows are disabled
- **WHEN** the hourly scheduler tick fires on the leader
- **THEN** the leader-gated pass runs and prunes purged pins without touching request logs or usage history
