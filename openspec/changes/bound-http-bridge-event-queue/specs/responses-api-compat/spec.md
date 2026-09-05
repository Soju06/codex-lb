## ADDED Requirements

### Requirement: Live queue reads do not spawn per-event tasks

Buffered and waiting HTTP-bridge live event reads MUST use the consumer task
without creating child tasks for item, terminal, revocation, or timeout signals.
Cancellation before consumption MUST leave an arriving event in the queue.
If a read deadline races an already-buffered event, the reader MUST return
that event before reporting the timeout.

#### Scenario: Waiting consumer receives an event

- **GIVEN** a live queue is empty and its consumer awaits the next event
- **WHEN** a producer publishes a live event or ordered terminal sequence
- **THEN** the consumer wakes without per-event child tasks
- **AND** cancellation before consumption leaves the payload and byte credit owned by the queue

### Requirement: HTTP bridge live event buffering is bounded

Each admitted HTTP-bridge Responses request MUST use a finite-capacity in-memory queue for live upstream events. When an attached downstream SSE consumer does not keep pace and that queue reaches capacity, the upstream relay MUST wait for downstream capacity before enqueueing another live event. In this finite-queue backpressure path, the relay MUST preserve event order and MUST NOT drop attached-consumer events to relieve pressure. Cancelling a pending queue read MUST NOT strand an event that another task removed from the queue, and timeout reconciliation MUST return a completed read before reporting the timeout. This no-drop guarantee does not apply after the process-wide byte budget rejects a payload and revokes the queue.

Across all live HTTP-bridge queues, retained event payload bytes MUST remain within a fixed process-wide internal budget. A payload MUST reserve its UTF-8 byte length before entering a queue and release that reservation when dequeued. If the budget cannot admit a payload, that request's queue MUST fail closed and revoke further producers; an attached stream MUST surface one `response.failed` terminal result with `upstream_unavailable` (or the equivalent HTTP 503 error when the route propagates HTTP errors), while a later upstream `response.failed` publication MAY be ignored by the revoked queue. A pre-consumer queue MAY be discarded only after explicit downstream detachment or another proof that no delayed generator can attach. Such an abandoned queue MAY expose only EOS to a delayed reader. The service MUST continue durable persistence, reservation settlement, request logging, and cleanup, and MUST record the pressure without exposing payload content or adding an operator setting.

The HTTP route MAY translate budget exhaustion into an HTTP 503 only before it has emitted its first stream event. Once any SSE event has been emitted, later budget exhaustion MUST remain inside the committed stream and emit the `response.failed` terminal result instead of raising a route-level HTTP error.

Downstream detachment or cancellation MUST release any relay wait on that request's full queue so the shared upstream reader and its enqueue tasks do not leak. Repeated cancellation while a blocked enqueue is cleaning up MUST NOT interrupt task reaping or release of the payload's process-wide byte reservation; cancellation MUST propagate only after that cleanup finishes. Revocation of downstream delivery MUST NOT prevent terminal persistence, reservation settlement, request logging, or request/session cleanup.

Failure finalization for an attached stream MUST publish its ordered terminal result without waiting for live queue capacity. A full queue and stalled attached consumer MUST NOT keep session lifecycle ownership from a later request, and the consumer MUST still receive every buffered event before the terminal result and end marker.

Completed durable transcript replay MUST remain byte-bounded by the durable spool contract and MUST use finite startup buffering that can hold the selected replay plus its end marker without waiting for a consumer that has not started yet.

#### Scenario: Paused consumer backpressures the live relay

- **GIVEN** an HTTP-bridge request with an attached downstream SSE consumer
- **WHEN** the consumer pauses long enough for the live event queue to reach capacity
- **THEN** the next live upstream enqueue waits without growing the queue beyond its finite capacity
- **AND** resuming the consumer delivers every event in order through the terminal event and end marker

#### Scenario: Paced consumer preserves delivery

- **GIVEN** an HTTP-bridge request whose downstream consumer keeps pace with upstream events
- **WHEN** ordinary and terminal Responses events are relayed
- **THEN** every event is delivered in upstream order
- **AND** reservation settlement, request logging, and request/session cleanup complete under their existing ownership rules

#### Scenario: Detached consumer releases a blocked producer

- **GIVEN** an HTTP-bridge live event enqueue is waiting because its downstream queue is full
- **WHEN** the downstream stream disconnects or is cancelled
- **THEN** the waiting enqueue and every enqueue-owned task terminate without requiring another consumer read
- **AND** terminal persistence, durable spool state, request logging, reservation settlement, and bridge cleanup remain able to complete

#### Scenario: Timeout cancellation retains a raced event

- **GIVEN** a live event becomes available while the stream reconciles a keepalive timeout
- **WHEN** timeout cleanup cancels the pending queue read
- **THEN** the event is either returned by that completed read or remains queued for the next read
- **AND** the event payload byte reservation is released exactly once when the consumer receives it

#### Scenario: Failure finalization does not wait for an attached consumer

- **GIVEN** an attached downstream consumer has stopped reading and its live queue is full
- **WHEN** websocket failure finalization publishes the request's terminal failure
- **THEN** finalization releases session lifecycle ownership without waiting for the consumer to drain a slot
- **AND** a later request can enter the session lifecycle section
- **AND** the stalled consumer later receives every buffered event before the terminal failure and end marker

#### Scenario: Durable replay starts without a live consumer

- **GIVEN** a completed durable operation has a replayable byte-bounded event transcript
- **WHEN** HTTP-bridge submission selects that replay before the downstream consumer loop starts
- **THEN** the finite replay queue accepts the selected transcript and end marker without deadlock
- **AND** the downstream consumer receives the complete replay in order

#### Scenario: Process byte budget fails closed

- **GIVEN** multiple live HTTP-bridge queues have retained payloads near the fixed process budget
- **WHEN** another payload cannot reserve its UTF-8 byte length
- **THEN** only the affected queue revokes producers and does not retain the rejected payload
- **AND** the retained payloads remain accounted until their queues dequeue them
- **AND** settlement, persistence, logging, and cleanup continue without an operator-configurable memory knob

#### Scenario: Budget revocation has one explicit terminal result

- **GIVEN** an HTTP-bridge stream has an attached downstream SSE consumer
- **WHEN** a live event cannot reserve bytes from the process-wide budget
- **THEN** the stream emits one `response.failed` event with `upstream_unavailable` (or returns HTTP 503 when HTTP errors are propagated)
- **AND** a later upstream `response.failed` event is not required to be delivered through the revoked queue
- **AND** the rejected payload and any unread queue bytes are released during cleanup

#### Scenario: Budget failure after stream commitment stays in SSE

- **GIVEN** an HTTP bridge route that propagates pre-stream failures as HTTP errors
- **AND** the route has already emitted at least one SSE event
- **WHEN** the process-wide live-event byte budget rejects a later event
- **THEN** the committed stream emits one `response.failed` event with `upstream_unavailable`
- **AND** the stream ends without raising a route-level HTTP 503

#### Scenario: Repeated cancellation releases a blocked reservation

- **GIVEN** a live-event producer whose payload reservation is waiting on a full queue
- **WHEN** cancellation is requested again while the producer is reaping its enqueue-owned tasks
- **THEN** the producer finishes reaping those tasks and releases the blocked payload reservation
- **AND** cancellation propagates only after the process-wide byte budget reflects that release

## MODIFIED Requirements

### Requirement: Responses WebSocket preserves bidirectional transport semantics

The Responses WebSocket relay MUST preserve ordered text and binary messages, selected subprotocol response metadata, close codes, and terminal error delivery across its downstream and upstream boundaries. Except for HTTP-bridge upstream WebSockets, which MUST use the bounded-delivery compatibility fallback until native per-stream flow control and cancellation are proven, direct and account-routed upstream connections MUST use native Codex-family WebSocket egress when the fixed helper is available before dispatch, while Python MUST retain route-aware endpoint selection, fallback safety, metadata, and cleanup. Ping and pong control frames MUST remain transport-owned and MUST NOT surface as application events. A frame whose native send acknowledgement is ambiguous or failed MUST NOT be replayed.

#### Scenario: Native direct relay preserves frames

- **GIVEN** a direct or account-routed Responses WebSocket uses the native helper
- **WHEN** text and binary frames travel in both directions
- **THEN** their type, payload, and ordering are preserved
- **AND** control ping and pong frames are handled below the application relay

#### Scenario: Native direct relay preserves terminal close

- **WHEN** the native upstream sends a close frame
- **THEN** the relay observes its close code and reason
- **AND** the native connection is removed from the helper's active registry

#### Scenario: Ambiguous native frame send fails closed

- **GIVEN** a downstream `response.create` frame is dispatched to the helper
- **WHEN** acknowledgement fails because the helper or connection closes
- **THEN** the turn surfaces a terminal transport failure
- **AND** the frame is not resent on another transport
