## ADDED Requirements

### Requirement: SSE keepalives preserve upstream timeout failures
The SSE keepalive injector MUST emit heartbeats only when its own idle interval expires. A TimeoutError raised by the upstream iterator MUST propagate and MUST NOT produce an unbounded heartbeat stream. Source cleanup and downstream cancellation MUST remain effective.

#### Scenario: Source times out after output
- **GIVEN** an upstream stream has delivered a frame
- **WHEN** its next chunk raises TimeoutError
- **THEN** the error propagates through the downstream streaming response
- **AND** the source closes without emitting substitute keepalives

#### Scenario: Source times out between heartbeat reads
- **GIVEN** the injector emitted an idle heartbeat while the next chunk was pending
- **WHEN** that pending chunk fails with TimeoutError before the consumer resumes
- **THEN** the next read propagates the upstream error and closes the source

#### Scenario: Source failure collides with idle expiry
- **GIVEN** the idle timer cancels the waiting read
- **AND** the pending source read fails before that waiting read resumes
- **WHEN** the consumer's same read resumes
- **THEN** it raises the original source exception without a heartbeat or heartbeat callback
- **AND** the wrapper and source close without another consumer read

#### Scenario: Source chunk or exhaustion collides with idle expiry
- **GIVEN** the idle timer cancels the waiting read
- **AND** the pending source read settles before that waiting read resumes
- **WHEN** the source settles with a normal chunk
- **THEN** the same consumer read yields that chunk without a heartbeat or heartbeat callback
- **WHEN** the source instead settles with StopAsyncIteration
- **THEN** the same consumer read ends the stream without a heartbeat or heartbeat callback

#### Scenario: Healthy source remains idle
- **WHEN** the injector's idle interval expires while the source is still pending
- **THEN** the injector emits a heartbeat and preserves the pending source read
