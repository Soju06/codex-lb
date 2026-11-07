## ADDED Requirements

### Requirement: Terminal zero-event bridge socket loss is replayed once

When a pooled HTTP Responses upstream websocket ends with a clean close, transient close or transport error before any response event and without buffered or visible output for its sole pending request, the bridge MUST retire the socket and replay at most once on a fresh connection. The original account MUST remain preferred unless unselectable; account-bound requests MUST retain their owner. Existing continuity, durable acknowledgement, cancellation, admission and request deadline guards MUST remain enforced. A previous_response_id that cannot safely cross the dead socket MUST fail closed under existing continuity rules. Transport loss MUST NOT authorize anchor removal. The bridge MUST log event=zero_event_replay with the outcome in detail.

#### Scenario: Clean close before first response event
- **GIVEN** a retry-safe unanchored request has seen zero response events and no output
- **WHEN** upstream closes with code 1000
- **THEN** a fresh socket receives one replay and its completed response reaches the client

#### Scenario: Replacement also closes
- **WHEN** the replacement closes before any response event
- **THEN** no third send occurs and stream_incomplete reaches the client

#### Scenario: Response progress blocks transport replay
- **WHEN** a socket ends after any response event or buffered output
- **THEN** this transport recovery MUST NOT replay the request

#### Scenario: Dead socket holds the only continuation anchor
- **GIVEN** a request carries previous_response_id without an existing safe continuity replay proof
- **WHEN** its socket ends without response events
- **THEN** no fresh socket replays the request and existing continuity failure behavior remains
