## Why

Dashboard guests currently inherit broad read access that includes full conversation archives and raw request identifiers. Those surfaces contain substantially more sensitive data than the aggregate operational statistics guests need.

## What Changes

- Require an admin dashboard principal for every conversation-archive endpoint.
- Keep request-log rows readable by guests, but redact raw client IP, user-agent, conversation ID, and archive lookup ID values.
- Reject the dedicated request-log `conversation_id` filter for guests while preserving admin filtering and conversation aggregates.
- Hide archive controls and raw identifying request metadata from guest dashboard views while preserving aggregate statistics.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `admin-auth`: Narrow guest read access for sensitive observability data.
- `proxy-runtime-observability`: Define role-aware exposure of archives and raw request metadata.
- `frontend-architecture`: Keep guest request-log views free of archive controls and raw identifying fields.

## Impact

Affected areas are dashboard authorization dependencies, conversation-archive routes, the request-log API response mapping and filtering boundary, request-log detail UI, and focused backend/frontend authorization tests. Persistence, proxy routing, retention, and admin aggregate statistics are unchanged.
