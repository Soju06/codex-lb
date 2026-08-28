## Why

Conversation archive records are keyed by the request context id, but successful Responses/WebSocket request-log rows can store the downstream response id in `request_id` for continuity lookup. The dashboard archive panel currently queries by the request-log `requestId`, so archived payloads can exist while the request detail view reports that none were found.

## What Changes

- Add nullable `request_logs.archive_request_id` as the archive lookup key for request-log rows.
- Expose `archiveRequestId` in the request-log API response.
- Preserve `requestId` semantics for response-id continuity lookup.
- Make the dashboard archive panel use `archiveRequestId` when present and fall back to `requestId` for older rows.

## Impact

- Existing request-log rows remain readable; older rows without `archive_request_id` continue to use `requestId`.
- The schema migration is additive and nullable.
