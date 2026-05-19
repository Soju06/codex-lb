## ADDED Requirements
### Requirement: API key cost accounting uses the billable service tier
API key cost accounting MUST continue to use the effective billable `service_tier` chosen for the request log and MUST NOT derive pricing from the operator-requested tier when the upstream reports a different actual tier.

#### Scenario: Requested and actual tiers differ
- **WHEN** a priced request is sent with `requested_service_tier: "priority"`
- **AND** the upstream reports `actual_service_tier: "default"`
- **THEN** the persisted billable `service_tier` is `default`
- **AND** API key cost accounting uses the `default` tier rate for that request
