## ADDED Requirements

### Requirement: Connection loss is recorded in the request scope

When an HTTP connection is lost before the in-flight response is complete, the server MUST record the loss kind — `eof` for a clean peer close, otherwise the exception type name reported to `connection_lost` — in that request's ASGI `scope["state"]` under the key `codex_lb.http_disconnected`, so the application can classify a late write as dropped. On an HTTP/1.1 pipelined connection the stamp MUST reach every request still open on it — the response currently being written and any parsed request queued behind it — regardless of which of them the server implementation currently tracks as its newest request. The stamp MUST write only into the existing per-request `scope["state"]` dict the server created for that request, MUST add no reference from timers or other server state to the protocol, and MUST NOT change the stock teardown (transport close on a clean close, no second close on an error close). A connection lost after the response completed MUST leave the key absent.

#### Scenario: Peer closes mid-response

- **WHEN** a client closes the connection (cleanly or with an error) while a streaming response is still being written
- **THEN** the request's `scope["state"]` carries the loss kind under `codex_lb.http_disconnected` before any application task resumes
- **AND** the request cycle is marked disconnected as before

#### Scenario: Loss after completion leaves no stamp

- **WHEN** a connection is lost after the response completed
- **THEN** the request's `scope["state"]` does not contain `codex_lb.http_disconnected`
- **AND** keep-alive timer release and transport teardown behave as specified elsewhere in this capability

#### Scenario: Peer closes while a pipelined request is queued

- **GIVEN** a second request was parsed and queued while the first response is still streaming
- **WHEN** the client closes the connection before the first response is complete
- **THEN** the first request's `scope["state"]` carries the loss kind under `codex_lb.http_disconnected`
- **AND** the queued request's `scope["state"]` carries it as well
- **AND** the queued request is not started on the lost connection
