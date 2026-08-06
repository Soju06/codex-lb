## ADDED Requirements

### Requirement: Unroutable upstream bridge events are logged

An HTTP bridge session multiplexes one upstream connection across its pending requests.
When an upstream event cannot be attributed to any pending request, the service MUST log
it once with the event type, whether the event carried a response id, and the count of
pending requests on that session. The log MUST NOT include raw prompt-cache keys,
session ids, response ids, or payload content.

Terminal bookkeeping events that are expected to arrive with no pending request — the
drain and retirement paths that already run after a session's requests have been
settled — MUST NOT be logged as unroutable, so the signal stays specific to events that
were dropped while work was still waiting.

#### Scenario: Event arrives with no pending request to receive it

- **GIVEN** an HTTP bridge session with at least one pending request
- **WHEN** an upstream event matches none of those pending requests
- **THEN** the service logs the event type and the pending-request count
- **AND** the log contains no response id, prompt-cache key, or payload content

#### Scenario: Routed events stay silent

- **WHEN** an upstream event is attributed to a pending request
- **THEN** no unroutable-event log is emitted for it
