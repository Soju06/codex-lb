# api-keys Delta

## ADDED Requirements

### Requirement: Stale usage-reservation reclamation enforces a hard age ceiling

Stale usage-reservation reclamation MUST reclaim `reserved` reservations whose
age exceeds a hard ceiling on creation time regardless of how recently their
`updated_at` was refreshed. This is the backstop for orphaned reservation
heartbeats: a leaked heartbeat task keeps touching `updated_at`, which would
otherwise exempt its reservation from the heartbeat-based staleness cutoff
forever. The ceiling MUST be far larger than any legitimate request lifetime
so it can never reclaim an in-flight reservation, and reclamation past the
ceiling MUST restore the reserved quota the same way heartbeat-based
reclamation does.

#### Scenario: Orphaned heartbeat cannot exempt a reservation forever

- **GIVEN** a `reserved` usage reservation created before the hard age ceiling
- **AND** a leaked heartbeat keeps refreshing its `updated_at`
- **WHEN** stale usage-reservation reclamation runs
- **THEN** the reservation is released and its reserved quota is restored

#### Scenario: Fresh reservations are untouched by the ceiling

- **GIVEN** a `reserved` usage reservation created within the hard age ceiling
- **AND** its `updated_at` is current
- **WHEN** stale usage-reservation reclamation runs
- **THEN** the reservation stays `reserved`
