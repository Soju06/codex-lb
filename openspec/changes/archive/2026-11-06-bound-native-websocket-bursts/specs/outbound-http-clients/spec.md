## ADDED Requirements

### Requirement: Native WebSocket delivery buffers bounded bursts

The native WebSocket delivery queue MUST accept ordered frame bursts within a 32 MiB queued-payload budget and 4096-message cap. It MUST charge UTF-8 text, the duplicated interpreted Responses payload, binary bytes and close reasons, and MUST release the charge on dequeue. A single frame at an empty queue MAY exceed the byte budget only within the existing transport frame-size limit. Saturation MUST fail and cancel only the affected native request with consumer_backpressure, preserving sibling requests and the shared helper. Delivery buffering MUST NOT block the pump from handling send or close acknowledgments.

#### Scenario: Relay drains a buffered response burst
- **GIVEN** a native helper emits more than 64 small ordered Responses frames within the delivery budget
- **WHEN** the relay yields between frames for processing and downstream delivery
- **THEN** every frame reaches downstream in order, including response.completed
- **AND** no consumer_backpressure failure replaces the response

#### Scenario: Consumer stalls past a delivery bound
- **GIVEN** a WebSocket consumer stops draining
- **WHEN** queued payload bytes or message count exceeds its bound
- **THEN** only that request is failed and cancelled
- **AND** sibling requests and their send acknowledgments remain usable

#### Scenario: Buffered frames precede command acknowledgments
- **GIVEN** more than 64 frames are buffered within the delivery budget
- **WHEN** the helper acknowledges a send or a close
- **THEN** the acknowledgment completes without waiting for downstream to drain those frames
