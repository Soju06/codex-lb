## ADDED Requirements

### Requirement: Resilience settings expose quota continuity recovery

The dashboard SHALL expose an accessible default-on `quotaFailoverEnabled`
switch in Advanced → Resilience, directly below Deterministic failover.
Its label SHALL be "Quota continuity recovery". Its description SHALL explain
soft-affinity release and verified full-history recovery without claiming a new
retry loop or retry limit. English, Korean, and Simplified Chinese strings
SHALL be provided. The switch SHALL use the existing settings API.

#### Scenario: Recovery is independently disabled

- **GIVEN** recovery and Deterministic failover are enabled
- **WHEN** the operator disables recovery
- **THEN** the saved recovery value is false
- **AND** the native resilience switches remain unchanged
- **AND** Routing does not display a duplicate recovery switch

#### Scenario: Existing default is retained

- **WHEN** a settings response omits `quotaFailoverEnabled`
- **THEN** the frontend resolves recovery to enabled
