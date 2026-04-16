## ADDED Requirements

### Requirement: Request logs display account plan tier
When a request log entry is associated with an account, the dashboard request-log API response MUST expose that account's `planType`, and the recent-requests table MUST render the plan tier in a visible request-log column or badge.

#### Scenario: Request log entry includes account plan type
- **WHEN** a request log entry is associated with an account whose `plan_type` is `plus`
- **THEN** the `GET /api/request-logs` response includes `planType: "plus"` for that row
- **AND** the dashboard recent-requests table renders the `plus` plan tier visibly for that row

#### Scenario: Legacy request log entry without account still renders
- **WHEN** a request log entry has no related account
- **THEN** the `GET /api/request-logs` response includes `planType: null` or omits it
- **AND** the dashboard recent-requests table still renders the row without failing
