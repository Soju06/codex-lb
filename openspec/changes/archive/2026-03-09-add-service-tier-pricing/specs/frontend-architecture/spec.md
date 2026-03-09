## ADDED Requirements

### Requirement: Request logs display fast-mode service tier
When a request log entry includes `service_tier`, the dashboard request-log API response MUST expose it and the recent-requests UI MUST render it alongside the model label.

#### Scenario: Fast-mode request log entry is visible
- **WHEN** a request log entry is recorded with `service_tier: "priority"`
- **THEN** the `GET /api/request-logs` response includes `serviceTier: "priority"`
- **AND** the dashboard recent-requests table renders the model label with the priority tier visible
