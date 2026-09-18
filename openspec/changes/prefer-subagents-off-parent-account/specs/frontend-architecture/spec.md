## ADDED Requirements

### Requirement: Dashboard controls subagent account preference
The routing settings UI MUST expose the persisted subagent account preference as `Off`, `Only when parent uses previous response IDs`, and `Always`. It MUST explain that the preference affects only fresh account-neutral children, that established child and hard continuity remain sticky, and that the parent account remains a fallback. Saving the control MUST use the existing dashboard settings update and error handling flow.

#### Scenario: Operator enables continuity-conditional preference
- **WHEN** an operator selects `Only when parent uses previous response IDs`
- **THEN** the dashboard persists `parent_bound_only`
- **AND** the displayed selection reflects the saved value

#### Scenario: Existing installation remains disabled
- **GIVEN** an installation has not configured the preference
- **WHEN** routing settings load after upgrade
- **THEN** the dashboard displays `Off`

