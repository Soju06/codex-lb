## ADDED Requirements

### Requirement: Support Responses websocket proxy transport
The service MUST accept WebSocket connections on `/backend-api/codex/responses` and `/v1/responses` and proxy Responses JSON events to the upstream ChatGPT Codex websocket endpoint for the selected account. The service MUST preserve request order for a single websocket connection, MUST continue to honor API key auth and request-limit enforcement, and MUST record request logs from terminal websocket response events.

#### Scenario: Backend Codex websocket request is proxied upstream
- **WHEN** a client connects to `/backend-api/codex/responses` over WebSocket and sends a valid `response.create` request
- **THEN** the service selects an upstream account, opens the upstream websocket for that account, forwards the request, and relays upstream response events back to the client

#### Scenario: Websocket upstream beta header is injected by default
- **WHEN** a client connects to `/v1/responses` over WebSocket without sending `OpenAI-Beta: responses_websockets=2026-02-06`
- **THEN** the service still adds the required Responses websocket beta token on the upstream handshake

#### Scenario: Websocket request preserves supported service tier upstream
- **WHEN** a client sends a websocket `response.create` request with a supported `service_tier`
- **THEN** the service forwards that `service_tier` upstream unchanged and uses it for websocket request accounting

#### Scenario: Websocket connect respects proxy request budget
- **WHEN** account selection, token refresh, or upstream websocket handshake would exceed the configured proxy request budget
- **THEN** the service fails the websocket request promptly with a stable `upstream_request_timeout` error instead of waiting for longer upstream timeouts

#### Scenario: No accounts available for websocket request
- **WHEN** a client sends a valid websocket `response.create` request and no active accounts are available
- **THEN** the service emits a websocket error event with a stable 5xx error payload and does not forward the request upstream
