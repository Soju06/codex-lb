## ADDED Requirements

### Requirement: Cancelled streamed responses do not re-cancel deferred startup work every loop iteration

When a streamed Responses body is cancelled by its response scope (client disconnect or request teardown) while the startup-probe first-item task or the SSE keepalive chunk task is still running cancellation-deferring cleanup, the proxy MUST NOT re-deliver cancellation to that task on every event-loop iteration. The body MUST await such tasks through a per-waiter proxy future so the level cancellation is absorbed by the proxy and the awaited task receives at most one explicit teardown cancellation. Teardown MUST still wait for that task to settle before closing the iterator chain it drives, so no `aclose()` is attempted on a running async generator and the task's eventual exception is retrieved.

#### Scenario: Probe task is cancelled once while its cleanup is blocked

- **GIVEN** a streamed response whose startup-probe task is waiting on cancellation-deferring cleanup that has not completed
- **WHEN** the response scope is cancelled and the event loop runs many iterations
- **THEN** the probe task's cancellation count stays at the single explicit teardown cancel
- **AND** the deferred cleanup task is not cancelled
- **AND** the response body task finishes once the cleanup settles, without spinning the loop meanwhile

#### Scenario: Keepalive teardown waits for the chunk task without respinning it

- **GIVEN** the SSE keepalive injector's pending chunk task is still driving a cancellation-deferring source when the consumer is cancelled
- **WHEN** the event loop runs many iterations before that source's cleanup settles
- **THEN** the chunk task is cancelled at most once
- **AND** the source iterator is not closed while the chunk task drives it
- **AND** the source iterator is closed exactly once after the chunk task settles
