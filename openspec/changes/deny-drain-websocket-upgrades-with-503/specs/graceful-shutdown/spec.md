# graceful-shutdown Delta

## MODIFIED Requirements

### Requirement: Graceful drain closes WebSocket admission

Once graceful drain begins, the application MUST reject every new external WebSocket connection before invoking the route handler. The rejection MUST be an HTTP denial response sent in place of the handshake (`websocket.http.response.start` followed by `websocket.http.response.body`), and MUST NOT be a pre-accept `websocket.close` frame, because ASGI servers surface a pre-accept close as an opaque HTTP `403` that clients treat as a terminal access error. The denial response MUST use HTTP status `503` and MUST carry a `Retry-After` header equal to the local overload retry-after value (`5`). For a proxy path (`/v1`, `/backend-api`, or any path below them) the body MUST be the OpenAI-style local-unavailable error envelope with `error.code = "proxy_unavailable"`, `error.type = "server_error"`, and `error.message = "Server is draining"`; for every other WebSocket path the body MUST be `{"detail": "Server is draining"}`. A Responses WebSocket scope admitted before the barrier MUST remain tracked until its handler exits. Other WebSocket protocols MUST receive the same late-admission rejection but MUST NOT hold the Responses in-flight counter for their full connection lifetime.

#### Scenario: New WebSocket arrives during drain

- **WHEN** a new WebSocket connection scope arrives after drain has begun
- **THEN** the application rejects the connection without invoking its route handler
- **AND** the rejected connection does not increase the in-flight count
- **AND** the rejection is an HTTP denial response with status `503` and `Retry-After: 5`, not a pre-accept `websocket.close` frame

#### Scenario: Proxy WebSocket upgrade is denied with the local-unavailable envelope

- **GIVEN** drain has begun
- **WHEN** a new WebSocket upgrade arrives at a proxy path such as `/v1/responses` or `/backend-api/codex/responses`
- **THEN** the client receives HTTP `503` with `Retry-After: 5`
- **AND** the JSON body carries `error.code = "proxy_unavailable"`, `error.type = "server_error"`, and `error.message = "Server is draining"`
- **AND** the server access log reflects `503` instead of `403 Forbidden`

#### Scenario: Non-proxy WebSocket upgrade is denied with a generic detail body

- **GIVEN** drain has begun
- **WHEN** a new WebSocket upgrade arrives at a non-proxy path such as `/ws/events`
- **THEN** the client receives HTTP `503` with `Retry-After: 5`
- **AND** the JSON body is `{"detail": "Server is draining"}`

#### Scenario: WebSocket crosses the drain barrier

- **WHEN** a Responses WebSocket scope is admitted immediately before drain begins
- **THEN** it remains in the in-flight count until its route handler exits
- **AND** shutdown waits for that scope within the configured drain timeout

#### Scenario: Realtime or Live connection predates drain

- **WHEN** a non-Responses WebSocket scope is admitted before drain
- **THEN** it does not hold the Responses in-flight counter
- **AND** normal Uvicorn connection shutdown remains its lifecycle bound
