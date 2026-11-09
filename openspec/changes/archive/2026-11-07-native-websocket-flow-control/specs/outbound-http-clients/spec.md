## ADDED Requirements

### Requirement: Native WebSocket consumers control upstream reads
The adapter MUST require `websocket_receive_credit_v1` before dispatch. Each socket MUST allow only one unconsumed data message across IPC. The helper MUST pause upstream reads when its receive credit is exhausted and resume when Python consumes the message. Accepted frames MUST NOT be discarded on consumer delay. Local send, close, cancellation and sibling operations MUST remain available during the pause. Pausing MUST NOT produce a pong timeout caused by the locally paused reader.

#### Scenario: Fast upstream and slow consumer
- **WHEN** upstream sends more than 64 data messages while the consumer pauses
- **THEN** the consumer receives every message in order after resuming
- **AND** no consumer-backpressure terminal replaces the response

#### Scenario: Paused socket does not block peers or shutdown
- **WHEN** one socket has exhausted receive credit
- **THEN** another socket can receive and acknowledge sends
- **AND** the paused socket can close or cancel without draining its data

### Requirement: Native WebSocket failures retain transport provenance
Native relay errors and logs MUST retain the failure phase, supplied close code and reason, and counts of data events and UTF-8 text/binary payload bytes received from that socket. Counters MUST exclude IPC metadata and control frames. Missing close details MUST remain unknown. Consumer-backpressure and helper failures MUST remain distinguishable from remote transport errors.

#### Scenario: Failure after data
- **WHEN** native egress fails after delivering data to the relay
- **THEN** the client-visible disconnect detail and log contain the phase, close details and socket payload counters

## MODIFIED Requirements

### Requirement: Native event dispatch schedules ready consumers

The native helper event reader MUST give ready response consumers a scheduling
opportunity between accepted events, including when helper output is already
buffered. A burst exceeding the per-request queue capacity MUST complete without
data loss when its consumer keeps draining. A stalled consumer MUST retain a
bounded queue and fail independently without blocking sibling requests.

Credit-controlled WebSockets MUST use the native WebSocket receive-credit contract instead of overflow-based failure. Their accepted data MUST remain buffered until consumed or explicitly closed by the caller.

#### Scenario: Buffered burst with active consumer

- **GIVEN** a helper emits more events than the queue capacity in one buffered burst
- **WHEN** the caller continuously consumes the response body
- **THEN** all body bytes arrive in order and the response completes

#### Scenario: Stalled consumer shares the helper

- **GIVEN** one caller stops consuming while another request shares its helper
- **WHEN** the stalled request exceeds its bounded event queue
- **THEN** only the stalled request fails and the other request completes

#### Scenario: Responses consumes buffered native bursts

- **GIVEN** direct or account-routed Responses uses the native helper
- **WHEN** framed SSE events, raw JSON success chunks, or raw HTTP error chunks arrive in a buffered burst exceeding queue capacity
- **THEN** an active consumer receives ordered SSE events, the complete JSON response, or the original HTTP error respectively
- **AND** the response is not replaced by a consumer-backpressure error

### Requirement: Native helper stream events are bounded per request by a byte budget

Events the native egress helper emits for one request MUST be buffered between the shared helper reader and that request's consumer in a per-request queue bounded by a queued-payload byte budget (32 MiB) together with an event-count cap (4096). Queued bytes MUST be released as the consumer drains. A burst of small framed events or a body made of large chunks that fits the byte budget MUST NOT fail a consumer that is still draining. Only when a request's queue exceeds its budget MAY the reader fail that request with `consumer_backpressure`, drop its queued events, cancel the helper-side request, and continue serving other requests.

Credit-controlled WebSockets MUST use the native WebSocket receive-credit contract instead of overflow-based failure. Their accepted data MUST remain buffered until consumed or explicitly closed by the caller.

#### Scenario: Burst of small framed events drains without failure

- **GIVEN** the helper has 2000 small body events for one request buffered in its output pipe
- **WHEN** the consumer starts reading after the burst landed
- **THEN** the consumer receives the complete body and the request is not failed

#### Scenario: A consumer that stops draining is bounded by bytes

- **GIVEN** a request whose consumer does not read while the helper emits 48 chunks of 1 MiB
- **WHEN** the queued payload exceeds 32 MiB
- **THEN** that request fails with `consumer_backpressure`
- **AND** other requests on the same helper keep being served
