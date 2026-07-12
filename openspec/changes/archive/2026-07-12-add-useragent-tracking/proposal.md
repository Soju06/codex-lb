## Why

The proxy already records request transport, tiers, and failure metadata, but it does not persist which prompt client produced a request. That leaves operators without a reliable way to inspect OpenCode client traffic in request logs or search by client family when debugging mixed HTTP and WebSocket traffic.

## What Changes

- Persist the full inbound `User-Agent` header on `request_logs` as `useragent`.
- Persist a derived indexed `useragent_group` value extracted from the first product token of the header.
- Capture and persist the same user-agent metadata for both HTTP and WebSocket request-log writes.
- Expose the new request-log fields through the dashboard request-log API and render the full value in the Request Details dialog.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: request-log persistence must include prompt-client user-agent metadata for HTTP and WebSocket proxy flows.
- `frontend-architecture`: dashboard request-log payloads and Request Details rendering must expose the stored user-agent metadata.

## Impact

- Affects `request_logs` database schema, including a new search index.
- Affects proxy request-log persistence in `app/modules/proxy/service.py` and `app/modules/request_logs`.
- Affects dashboard request-log schemas and the Request Details dialog UI.
- Requires backend and frontend regression coverage for null handling and transport parity.
