## MODIFIED Requirements

### Requirement: Warmup target pool is derived from API-key account scope
The warmup target pool SHALL be derived from the authenticated API key. If `account_assignment_scope_enabled=true`, only assigned request-routable accounts SHALL be considered. If account scope is not enabled, all active and `reauth_required` accounts SHALL be considered. Paused and deactivated accounts MUST be excluded.

#### Scenario: Scoped API key warms only assigned accounts
- **WHEN** an API key has account scope enabled with assigned accounts
- **THEN** warmup only evaluates and submits requests for assigned accounts whose status is active or `reauth_required`

#### Scenario: Unscoped API key warms all active accounts
- **WHEN** an API key has account scope disabled
- **THEN** warmup evaluates and submits requests against all active and `reauth_required` accounts
- **AND** paused and deactivated accounts are not submitted
