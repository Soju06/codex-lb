## ADDED Requirements

### Requirement: HTTP middleware relays responses in the request task

Every middleware in the HTTP middleware stack MUST be a pure ASGI middleware that invokes the downstream application in the same task and forwards response messages directly. The stack MUST NOT include Starlette `BaseHTTPMiddleware` (including `@app.middleware("http")` registrations). Response bodies forwarded on success paths MUST be byte-identical to the downstream application's output.

#### Scenario: Production middleware stack contains no BaseHTTPMiddleware

- **WHEN** the production application is constructed
- **THEN** no registered middleware entry is `starlette.middleware.base.BaseHTTPMiddleware`

#### Scenario: Streaming body is forwarded unchanged

- **WHEN** a route returns a streaming response through the middleware stack
- **THEN** the sequence of ASGI response messages, including headers, body bytes, and `more_body` flags, is identical to the sequence emitted without the middleware

#### Scenario: Mid-stream failure propagates without a synthetic terminator

- **WHEN** a response body generator raises after at least one body chunk has been sent
- **THEN** the exception propagates to the ASGI server
- **AND** the stack does not emit an additional `http.response.body` message with `more_body=false` before propagating
