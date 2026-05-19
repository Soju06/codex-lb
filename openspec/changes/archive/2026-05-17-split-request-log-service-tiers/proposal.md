## Why
`request_logs.service_tier` currently serves two different purposes:

- billable/effective tier for pricing and summaries
- operator-visible tier for understanding what the client requested versus what the upstream actually used

That overload makes the dashboard misleading when the upstream downgrades or omits the echoed tier. Operators cannot tell whether `default` was requested, selected by the upstream, or inferred as a fallback.

## What Changes
- add explicit `requested_service_tier` and `actual_service_tier` fields to persisted request logs
- keep `service_tier` as the effective billable tier used for pricing rollups and cost limits
- expose all three values through `/api/request-logs`
- update the dashboard request-log UI to show the actual tier as the visible tier and surface the requested tier when it differs

## Impact
- DB: `request_logs.requested_service_tier`, `request_logs.actual_service_tier`
- Backend: Responses proxy request-log settlement and request-log API schemas
- Frontend: recent request model label and tier metadata
- Specs: `openspec/specs/responses-api-compat/spec.md`, `openspec/specs/frontend-architecture/spec.md`, `openspec/specs/api-keys/spec.md`
