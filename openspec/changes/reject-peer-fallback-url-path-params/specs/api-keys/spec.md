## MODIFIED Requirements

### Requirement: API Key update

The system SHALL allow updating key properties via `PATCH /api/api-keys/{id}`. Updatable fields: `name`, `allowedModels`, `weeklyTokenLimit`, `expiresAt`, `isActive`, `assignedAccountIds`, and `peerFallbackBaseUrls`. The key hash and prefix MUST NOT be modifiable. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt` and normalize them to UTC naive before persistence.

#### Scenario: Reject peer fallback URLs with params, query, or fragment
- **WHEN** admin submits `POST /api/api-keys` or `PATCH /api/api-keys/{id}` with a peer fallback base URL containing path params, query, or fragment
- **THEN** the system rejects the request with a dashboard validation error
- **AND** it does not persist the invalid peer fallback URL
