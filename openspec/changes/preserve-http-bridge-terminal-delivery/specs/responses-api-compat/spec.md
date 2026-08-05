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
The completed-queue claim and the terminal idle-timeout decision MUST be
serialized under the bridge pending lock. If completed processing wins that
serialization and claims a live queue, the timeout MUST be suppressed. If the
terminal event and end-of-stream marker are already queued when a concurrent
timeout finishes awaited recovery work, the completed claim MUST remain
authoritative until the stream consumes that queued delivery. If the
terminal idle timeout wins while no completed delivery is active, it MUST
revoke the request's mutable event queue before releasing the pending lock so a
later completed event cannot claim an orphaned queue.

The first idle-timeout suppression for one completed-delivery operation MUST
emit one bounded diagnostic containing the request ID, downstream response ID,
and elapsed seconds. Further liveness intervals for that same operation MUST
NOT repeat the diagnostic.

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
- **AND** it logs the suppression once with request, response, and elapsed-time context

#### Scenario: Terminal idle timeout wins before completed processing

- **GIVEN** an HTTP bridge stream has exhausted its configured idle window
- **AND** no completed-delivery operation has claimed its queue
- **WHEN** the stream acquires the bridge pending lock before a concurrent completed event
- **THEN** it revokes the mutable event queue while still holding that lock
- **AND** it emits the existing synthetic idle failure
- **AND** later completed processing does not deliver to the revoked queue

#### Scenario: Completed delivery finishes during timeout recovery

- **GIVEN** an HTTP bridge timeout path is awaiting pre-response recovery work
- **AND** completed processing claims the live queue and enqueues its terminal event and end-of-stream marker
- **WHEN** completed processing returns before the timeout path rechecks ownership
- **THEN** the completed claim remains authoritative
- **AND** the stream consumes the queued completion without emitting a synthetic idle failure

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
