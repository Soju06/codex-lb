## ADDED Requirements

### Requirement: Recognized Pro-family plans share the coarse Pro bucket

Telemetry snapshots MUST count `promax` and the Business Pro Lite alias
`self_serve_business_prolite` in the existing `pro` plan-count bucket, alongside
`pro` and `prolite`. Snapshots MUST retain the existing four coarse plan buckets
and MUST NOT expose these upstream plan identifiers or exact account counts.

#### Scenario: Paid-plan account is counted as Pro

- **GIVEN** an account whose plan is `promax` or `self_serve_business_prolite`
- **WHEN** the telemetry snapshot is built
- **THEN** the account contributes to the existing `pro` plan-count bucket
- **AND** its upstream plan identifier is not added to the snapshot
