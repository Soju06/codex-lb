## ADDED Requirements

### Requirement: Websocket connect-failure cleanup MUST survive client disconnect

The service MUST complete local cleanup and observability side effects when a
Responses websocket request hits a proxy-generated connect failure before an
upstream websocket is established, even if the downstream client disconnects
before the failure frame can be sent. Expected downstream disconnects during
that final send MUST NOT escape as ASGI application exceptions.

#### Scenario: Client disconnects before connect-failure frame is sent

- **WHEN** a Responses websocket connect attempt fails before upstream
  establishment
- **AND** the downstream client disconnects before the proxy can send the
  generated failure frame
- **THEN** the service releases any API-key reservation for the request
- **AND** it releases any response-create admission gate held by the request
- **AND** it records the connect failure in request logs
- **AND** it does not raise the downstream disconnect as an ASGI application
  exception
