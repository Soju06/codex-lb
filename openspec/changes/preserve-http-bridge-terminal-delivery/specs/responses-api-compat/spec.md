# responses-api-compat Delta

## ADDED Requirements

### Requirement: Claimed HTTP bridge completed queues remain deliverable

When HTTP bridge processing of `response.completed` removes a request from
pending ownership, it MUST retain the request's downstream event queue for the
remainder of that completed operation. Later asynchronous bookkeeping or
request detachment MUST NOT revoke that claimed queue before the completed
operation's selected terminal event and end-of-stream marker are enqueued. If
fail-closed bookkeeping replaces the upstream completion with a terminal
failure, that selected failure event is the terminal event governed by this
requirement.

While the claimed completed-delivery operation remains active, ordinary stream
idle accounting MUST NOT replace the upstream completion with a synthetic idle
failure, and the stream MUST continue emitting its existing liveness frames.
When that operation returns, raises, or is cancelled before delivery, idle
timeout behavior MUST resume.

If detachment removes the request from pending ownership first, existing
client-disconnect and drain behavior MUST remain unchanged.

#### Scenario: Completed processing claims the request before detachment

- **GIVEN** an HTTP bridge stream is waiting on its request event queue
- **AND** an upstream `response.completed` event removes that request from pending ownership
- **WHEN** request detachment overlaps later completed-event bookkeeping
- **THEN** the stream receives the terminal event selected for downstream delivery exactly once
- **AND** the stream receives its end-of-stream marker

#### Scenario: Completed bookkeeping exceeds the idle window

- **GIVEN** completed-event processing has claimed a live request queue
- **WHEN** later completed bookkeeping exceeds the configured stream idle window
- **THEN** the stream continues emitting liveness frames
- **AND** it does not emit a synthetic idle failure while that operation remains active

#### Scenario: Completed bookkeeping aborts

- **GIVEN** completed-event processing has claimed a live request queue
- **WHEN** that completed-delivery operation exits without enqueueing its terminal event
- **THEN** idle timeout suppression ends
- **AND** the existing idle-timeout failure behavior resumes

#### Scenario: Detachment claims the request first

- **GIVEN** an HTTP bridge request is still pending
- **WHEN** detachment removes downstream queue ownership before completed-event matching
- **THEN** existing client-disconnect and upstream-drain behavior is preserved
- **AND** no completed event is delivered to another request
