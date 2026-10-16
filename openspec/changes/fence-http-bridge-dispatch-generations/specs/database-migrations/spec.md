## ADDED Requirements

### Requirement: Dispatch migration preserves the existing database

The dispatch-generation migration MUST add one nullable integer column without a default or backfill. Legacy rows MUST retain NULL generation; new aware rows MUST explicitly start at zero. Legacy ambiguous operations MUST fail closed for writes that require dispatch authority.

#### Scenario: An existing database is upgraded

- **WHEN** the additive migration runs on a database copy
- **THEN** existing rows and unrelated schema remain unchanged
- **AND** legacy rows retain NULL dispatch generation
- **AND** legacy in-flight operations cannot gain generation-aware write or redispatch authority

### Requirement: Dispatch-generation upgrade requires writer quiescence

The dispatch-generation migration MUST NOT be presented as rolling-safe. Every
replica running pre-generation code MUST be stopped before the migration runs.
The migration runner MUST warn before applying the revision to an existing
HTTP-bridge operation schema, while recognizing that it cannot itself prove
whether old replicas are live.

#### Scenario: Helm upgrade crosses the dispatch-generation migration

- **GIVEN** the chart runs migrations in a `pre-upgrade` hook before the
  StatefulSet rolling replacement
- **WHEN** an operator upgrades from a pre-generation release
- **THEN** the Kubernetes guide MUST require scaling the old StatefulSet to
  zero and waiting for its pods to stop before `helm upgrade`
- **AND** it MUST permit an equivalent blue/green or manual-migration sequence
  only when all old writers are already stopped
- **AND** it MUST state that no mixed-version writer window is supported
