## ADDED Requirements

### Requirement: Routed streaming upstream responses are released when the consumer stops before EOF

When an upstream streaming request is issued through a resolved upstream proxy route,
the response body is consumed unbuffered and the consumer routinely stops before the
body reaches EOF: on the terminal stream event, on the stream idle timeout, on
cancellation, on downstream disconnect, and when the response is mapped to an error
before the body is drained. On every such exit the proxy MUST release or close the
upstream response object before it closes the per-stream client that owns the
connection, so the connection is returned or closed synchronously and no connection
object is left to be finalized by the garbage collector.

The release MUST work for every response shape the routed path can receive: an
aiohttp response (`release()`), a native egress response (`aclose()`), and a buffered
or duck-typed response that exposes neither (no-op). Responses obtained through a
SOCKS route MUST release the wrapped response before closing the private session that
carried it.

Release MUST run only after the last event block has been yielded to the consumer and
MUST NOT change the forwarded bytes, the error mapping, the retry classification, or
the cancellation semantics of the stream.

#### Scenario: Terminal event arrives while upstream holds the connection open

- **GIVEN** a routed HTTP stream whose upstream emits `response.completed` and then keeps the connection open
- **WHEN** the proxy stops reading on the terminal event
- **THEN** the upstream response is released before the per-stream client is closed
- **AND** no `Unclosed connection` event is reported to the event loop exception handler after a full garbage collection
- **AND** the forwarded event blocks are byte-identical to the upstream frames

#### Scenario: Stream idle timeout

- **GIVEN** a routed HTTP stream whose upstream goes silent after the first event
- **WHEN** the stream idle timeout elapses
- **THEN** the synthetic `stream_idle_timeout` failure event is yielded as before
- **AND** the upstream response is released before the per-stream client is closed

#### Scenario: Cancellation or downstream disconnect mid-stream

- **GIVEN** a routed HTTP stream that is cancelled, or whose consumer calls `aclose()`, while a body read is pending
- **WHEN** the stream generator unwinds
- **THEN** the upstream response is released before the per-stream client is closed
- **AND** the cancellation propagates to the caller unchanged

#### Scenario: Error status mapped before the body is drained

- **GIVEN** a routed HTTP stream whose upstream answers with a non-2xx status
- **WHEN** the proxy raises the mapped `ProxyResponseError`
- **THEN** the upstream response is released before the per-stream client is closed

#### Scenario: Response without a release method

- **GIVEN** a routed response object that exposes neither `release()`, `close()` nor `aclose()`
- **WHEN** the stream ends
- **THEN** teardown is a no-op for the response and the stream result is unchanged
