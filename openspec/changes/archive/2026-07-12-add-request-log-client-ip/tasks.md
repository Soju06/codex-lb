## 1. Database

- [x] 1.1 Add nullable `request_logs.client_ip`.
- [x] 1.2 Add an index on `request_logs.client_ip`.

## 2. Backend

- [x] 2.1 Persist `client_ip` in `RequestLogsRepository.add_log()`.
- [x] 2.2 Resolve client IP using the existing trusted-proxy request-locality helper.
- [x] 2.3 Pass client IP through HTTP/SSE/WebSocket Responses request-log paths.
- [x] 2.4 Preserve original client IP across signed HTTP bridge owner forwarding.
- [x] 2.5 Include `client_ip` in request-log search.
- [x] 2.6 Bind HTTP bridge upstream sends to the retried request archive id.

## 3. API and Frontend

- [x] 3.1 Expose `clientIp` in request-log API entries.
- [x] 3.2 Parse nullable `clientIp` in the dashboard schema.
- [x] 3.3 Render `Client IP` in request details with copy support when present.

## 4. Validation

- [x] 4.1 Add repository/API/search regression coverage.
- [x] 4.2 Add frontend schema and request-details coverage.
- [x] 4.3 Add HTTP bridge replay archive-id regression coverage.
- [x] 4.4 Run backend, frontend, OpenSpec, lint, and type checks.
