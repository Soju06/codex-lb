## ADDED Requirements

### Requirement: Scoped pooled usage with unquantified accounts

API-key pooled usage SHALL expose per-window unquantified-account counts and
SHALL return null whole-pool remaining percentages for affected windows.
Counts SHALL use the same account assignment, status and privacy filters
as the existing pooled credit calculation.

#### Scenario: Assigned Pro-only pool
- **WHEN** a key is assigned only a Pro account despite another Pro Max
  account existing in the service
- **THEN** its pooled usage and percentage retain existing behavior
- **AND** its unquantified-account counts are zero

#### Scenario: Reported versus absent windows
- **WHEN** an assigned active Pro Max account reports only weekly usage
- **THEN** its weekly count is 1 and its short-window count is 0
- **AND** paused, deactivated or unassigned accounts do not contribute
