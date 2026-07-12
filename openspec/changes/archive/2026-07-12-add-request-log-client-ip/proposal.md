# Add request-log client IP metadata

## Why

Request logs currently identify the account, API key, model, transport, and user-agent, but they do not preserve the client IP that reached the proxy. Operators need that secondary attribution signal when an API key is deleted, shared across hosts, or used from an unexpected location.

## What Changes

- Persist a nullable `client_ip` value on `request_logs`.
- Resolve client IP at the proxy edge with the existing trusted-proxy header policy.
- Carry the resolved client IP through internal HTTP bridge forwarding so owner-side request logs keep the original edge client.
- Expose `clientIp` in request-log API responses and dashboard request details.
- Include `client_ip` in request-log search.

## Impact

- Adds an Alembic migration for a nullable `request_logs.client_ip` column and index.
- Affects HTTP/SSE/WebSocket Responses request logs and dashboard request-log consumers.
