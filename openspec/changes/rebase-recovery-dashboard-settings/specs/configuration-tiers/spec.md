## ADDED Requirements

### Requirement: Recovery controls remain in the configuration-tier migration backlog

The ten HTTP bridge transcript and recovery controls introduced by this change
MUST be classified as T3 behaviour tunables in `SETTING_TIERS`, because their
values are fleet-wide runtime policy rather than per-replica topology. Until a
dashboard migration lands, each field MUST have a `MIGRATING` entry with the
value `backlog`. The follow-up dashboard migration MUST add nullable
`dashboard_settings` homes, shared resolver/cache consumption, and settings API
provenance without changing the controls' defaults or opt-in safety gates.

#### Scenario: Recovery controls are explicitly tracked

- **WHEN** the configuration-tier checker runs on this change
- **THEN** all ten recovery controls are reported as T3
- **AND** each has a `MIGRATING` entry
- **AND** no control is classified as T4 merely because its dashboard home is
  not yet implemented.

#### Scenario: Dashboard migration completes the backlog

- **WHEN** the follow-up migration adds a dashboard home for a recovery control
- **THEN** its `MIGRATING` entry is removed in the same change
- **AND** the dashboard value takes precedence over the environment fallback
  through the shared `SettingsCache` resolver.
