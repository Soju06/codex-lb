## ADDED Requirements

### Requirement: Timed-out startup probes MUST settle first-item task exceptions

The proxy MUST retrieve eventual first-item task exceptions when a Responses or
chat-completions startup error probe times out while its first-item task is
still running and the returned stream is abandoned before iteration resumes.
This MUST prevent unhandled asyncio task diagnostics such as `Task exception was
never retrieved` or shielded-future exception logs for upstream
`ProxyResponseError` failures that arrive after the probe timeout.

If the returned stream is consumed later, the task result or exception MUST
remain observable through normal stream iteration.

#### Scenario: Abandoned timed-out probe consumes first-item exception

- **GIVEN** a startup probe times out before the first upstream stream item is available
- **AND** the first-item task later raises `ProxyResponseError`
- **WHEN** the request path abandons the returned stream before consuming that task
- **THEN** the event loop does not emit an unhandled task-exception diagnostic
- **AND** task ownership is settled without changing the client-visible result

#### Scenario: Consumed timed-out probe preserves stream behavior

- **GIVEN** a startup probe times out before the first upstream stream item is available
- **WHEN** the caller later iterates the returned stream
- **THEN** the first task's result or exception is still yielded or raised through the returned stream
