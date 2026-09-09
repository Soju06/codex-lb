## MODIFIED Requirements

### Requirement: Native WebSocket transport-event queues do not impose a fixed event-count limit

The Python native-egress adapter MUST NOT impose a fixed event-count capacity on an individual native WebSocket transport-event queue and MUST NOT terminate an otherwise healthy WebSocket solely because more than a fixed number of helper events are pending. It MUST preserve event ordering, request isolation, cancellation, helper-generation failure handling, and terminal delivery. Transport-event and decoded application-message queues MUST share bounded per-connection and per-helper byte budgets. Exhausting a data budget MUST preserve already accepted messages and terminal delivery, isolate the overflowing connection, and release charges during consumption and cleanup. Native HTTP SSE framing MUST NOT weaken these WebSocket guarantees.

#### Scenario: Bursty native WebSocket events exceed the former limit

- **GIVEN** a native WebSocket helper emits more than 64 ordered transport events for one connection before its relay task drains them
- **WHEN** the relay consumes the connection and the byte budgets are not exceeded
- **THEN** every event is delivered in order through the existing WebSocket API
- **AND** the adapter does not synthesize a `consumer_backpressure` failure from the transport-event queue

#### Scenario: Application-message backpressure remains bounded

- **GIVEN** a native WebSocket consumer stops draining application messages
- **WHEN** incoming messages exceed the connection byte budget
- **THEN** the adapter delivers the accepted prefix followed by the bounded overflow failure and cleans up the native request
- **AND** another connection can continue and released charges remain available for later traffic

#### Scenario: Raw and decoded stages share the helper ceiling

- **WHEN** raw events and decoded messages coexist across connections in one helper generation
- **THEN** both stages consume their connection and shared helper byte budgets
- **AND** moving or clearing messages releases their previous stage's charges
