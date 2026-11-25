## ADDED Requirements

### Requirement: Live queue writes do not spawn per-event tasks

HTTP-bridge live event writes MUST wait for finite queue capacity in the
producer task without creating child enqueue, revocation, or cleanup tasks.
A blocked payload MUST retain one process-wide byte reservation owned by that
producer until the payload is enqueued or the wait ends. Cancellation or
revocation before enqueue MUST release that reservation before returning,
without an asynchronous cleanup wait. A producer cancelled after a capacity
wakeup MUST NOT insert its payload or strand another waiting producer.

#### Scenario: Blocked producer resumes without child tasks

- **GIVEN** a live queue is full and a producer waits with a reserved payload
- **WHEN** its consumer drains a slot
- **THEN** the producer enqueues that payload without spawning a child task
- **AND** dequeue releases its byte reservation exactly once

#### Scenario: Cancellation races a capacity wakeup

- **GIVEN** multiple producers are waiting on a full live queue
- **WHEN** a slot is drained and one awakened producer is cancelled before enqueue
- **THEN** its payload reservation is released without inserting the payload
- **AND** another waiting producer can use the available slot

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

Each admitted HTTP-bridge Responses request MUST use a finite-capacity in-memory queue for live upstream events. When an attached downstream SSE consumer does not keep pace and that queue reaches capacity, the upstream relay MUST wait for downstream capacity before enqueueing another live event. In this finite-queue backpressure path, the relay MUST preserve event order and MUST NOT drop attached-consumer events to relieve pressure. Cancelling a pending queue read MUST NOT strand an event that another task removed from the queue, and timeout reconciliation MUST return a completed read before reporting the timeout. After delivery-stall expiry or process-wide byte-budget rejection revokes the queue, the retained-prefix and explicit failure contract applies instead.

Across all live HTTP-bridge queues, retained event payload bytes MUST remain within a fixed process-wide internal budget of 256 MiB, including payloads held by blocked producers. Completed replay MUST retain its separate durable spool contract; aggregate completed replay bytes and total process memory are outside this live-payload requirement. A payload MUST reserve its UTF-8 byte length before entering a queue and release that reservation when dequeued. If the budget cannot admit a payload, that request's queue MUST fail closed and revoke further producers; an attached stream MUST surface one `response.failed` terminal result with `upstream_unavailable` (or the equivalent HTTP 503 error when the route propagates HTTP errors), while a later upstream `response.failed` publication MAY be ignored by the revoked queue. A pre-consumer queue MAY be discarded only after explicit downstream detachment or another proof that no delayed generator can attach. Such an abandoned queue MAY expose only EOS to a delayed reader. The service MUST continue durable persistence, reservation settlement, request logging, and cleanup, and MUST record the pressure without exposing payload content or adding an operator setting.

The HTTP route MAY translate budget exhaustion into an HTTP 503 only before it has emitted its first stream event. Once any SSE event has been emitted, later budget exhaustion MUST remain inside the committed stream and emit the `response.failed` terminal result instead of raising a route-level HTTP error.

Downstream detachment or cancellation MUST release any relay wait on that request's full queue so the shared upstream reader does not leak. Repeated cancellation MUST NOT interrupt release of the blocked payload's process-wide byte reservation; cancellation MUST propagate only after that synchronous cleanup finishes. Revocation of downstream delivery MUST NOT prevent terminal persistence, reservation settlement, request logging, or request/session cleanup.

Failure finalization for an attached stream MUST publish its ordered terminal result without waiting for live queue capacity. A full queue and stalled attached consumer MUST NOT keep session lifecycle ownership from a later request, and the consumer MUST still receive every buffered event before the terminal result and end marker.

If the request's enqueue deadline expires before an event payload is accepted, the live queue MUST retain that delivery failure independently of producer revocation. The downstream consumer MUST receive the retained prefix followed by `response.failed` with `request_timeout` and EOS, never a later successful completion after dropped output. If the failure payload cannot fit the process byte budget, the existing fail-closed budget result MAY replace it. Terminal persistence and settlement MUST still complete for the upstream result. Expiry while adding only EOS after an accepted terminal payload MUST preserve that payload and append EOS without inventing a payload loss.

Completed durable transcript replay MUST remain byte-bounded by the durable spool contract and MUST use finite startup buffering that can hold the selected replay plus its end marker without waiting for a consumer that has not started yet.

#### Scenario: Owner recovery closes an attached child stream

- **GIVEN** owner-forward recovery is yielding an attached local child stream while completion owns its queue
- **WHEN** the downstream caller closes the owner-recovery stream
- **THEN** the wrapper MUST await the child stream's closure before its outer detachment
- **AND** child cleanup MUST revoke downstream delivery without relying on asynchronous-generator garbage collection

#### Scenario: Terminal flush loses deferred output at the enqueue deadline

- **GIVEN** an attached consumer is paused and a terminal frame flushes more deferred reasoning events than its finite queue can retain
- **WHEN** a deferred payload enqueue reaches the request deadline before the consumer resumes
- **THEN** the producer and its owned tasks finish without another downstream read
- **AND** the consumer receives the retained prefix followed by a timeout failure and EOS, not successful completion
- **AND** terminal persistence, settlement, and queue byte release still complete

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
- **WHEN** cancellation is requested repeatedly before the producer resumes
- **THEN** the producer releases the blocked payload reservation without an asynchronous cleanup wait
- **AND** cancellation propagates only after the process-wide byte budget reflects that release

### Requirement: Continuous live delivery stall terminates only the affected request

An attached request whose full live queue prevents enqueue progress MUST stop waiting at a finite per-request continuous delivery-stall bound, independently of the overall request budget. Expiry MUST return control to the shared reader without closing the shared session or failing a healthy sibling. When expiry rejects an event payload, the delayed consumer MUST receive its retained prefix followed by one explicit failure and EOS, never success after rejected output. Expiry of only EOS after an accepted terminal payload MUST preserve that payload and append EOS. Revocation MUST release blocked-producer byte reservations before returning; retained payloads MUST remain accounted until consumed or explicitly abandoned. Upstream persistence and API-key settlement MUST retain their existing ownership and complete without another downstream read.

#### Scenario: Paused delivery releases a healthy same-session sibling

- **GIVEN** attached requests A and B share an upstream session and both request deadlines are later than A's delivery-stall bound
- **AND** A's live queue is full and its next payload blocks the shared reader
- **WHEN** A reaches the continuous delivery-stall bound without another downstream read
- **THEN** A's blocked enqueue returns and its blocked-payload reservation is released
- **AND** B's queued upstream events progress to successful completion before either request deadline
- **AND** A later receives its retained prefix followed by failure and EOS
- **AND** A's retained byte reservations are released on consumption or explicit abandonment
- **AND** upstream terminal persistence and API-key settlement complete exactly once under their existing ownership

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
