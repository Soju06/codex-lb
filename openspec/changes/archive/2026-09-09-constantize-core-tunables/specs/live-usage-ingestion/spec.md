## ADDED Requirements

### Requirement: Live ingestion is decoupled and always on

The core client layer SHALL publish snapshots through a hub that no-ops until the module layer registers an ingestor at startup. The module layer SHALL register the ingestor unconditionally at startup; there is no operator switch for live ingestion, and `CODEX_LB_LIVE_USAGE_INGESTION_ENABLED` is a removed setting that startup reports and ignores.

#### Scenario: Removed kill switch is ignored

- **WHEN** the process starts with `CODEX_LB_LIVE_USAGE_INGESTION_ENABLED=false`
- **THEN** the ingestor is still registered and proxied responses produce usage writes
- **AND** startup logs the removed-setting warning once

#### Scenario: Unregistered hub is inert

- **WHEN** snapshots are published before an ingestor is registered
- **THEN** they are discarded without error

## REMOVED Requirements

### Requirement: Live ingestion is decoupled and switchable

**Reason**: Renamed to "Live ingestion is decoupled and always on"; the environment kill switch was never used and is constantized (always enabled).

**Migration**: Remove `CODEX_LB_LIVE_USAGE_INGESTION_ENABLED` from the environment; startup warns once while it is still set.
