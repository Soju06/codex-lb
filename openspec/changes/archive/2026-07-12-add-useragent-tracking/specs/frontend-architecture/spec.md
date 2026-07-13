## ADDED Requirements

### Requirement: Dashboard request-log details expose user-agent metadata
The dashboard request-log API response MUST expose the persisted request-log `useragent` and `useragentGroup` values when present. The Request Details dialog MUST render the full `useragent` value in a `User Agent` field below the `Transport`, `Time`, and `Error Code` row, and MUST render `—` when no full user-agent value is stored.

#### Scenario: Request details show the full stored user-agent
- **WHEN** a request log entry is stored with `useragent: "opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14"` and `useragentGroup: "opencode"`
- **THEN** the `GET /api/request-logs` response includes both values for that row
- **AND** the Request Details dialog shows `User Agent` with the full stored string

#### Scenario: Request details show a placeholder for legacy rows
- **WHEN** a request log entry has `useragent: null`
- **THEN** the `GET /api/request-logs` response includes `useragent: null` and `useragentGroup: null` or omits them as nullable fields
- **AND** the Request Details dialog renders `User Agent` as `—`
