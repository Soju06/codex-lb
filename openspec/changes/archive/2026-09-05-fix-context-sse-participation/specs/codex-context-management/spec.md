## MODIFIED Requirements

### Requirement: Durable history participation
Authenticated Responses with `reasoning.context=all_turns` and a canonical `client_metadata.session_id` SHALL bind session/API-key ownership before HTTP, HTTP-bridge or native WebSocket dispatch. HTTP streaming SHALL record participation only after parsing an upstream event with a classified event type; startup failures, empty streams, SSE comments and unclassified frames before that event MUST NOT add a participant. Leading non-event frames MUST NOT prevent recording a later parsed event. HTTP-bridge and native WebSocket participation SHALL persist before send. A rejected attempt after these observation boundaries MAY leave an empty history participant. Native WebSocket requests MUST set their sent timestamp immediately before send, without an intervening await. Context calls MUST validate the root session UUID and current agent path and preserve child-agent query fields. History queries SHALL contact every recorded participant, or the notes owner when none exists, within the current key scope. Ownership and participant records MUST survive process restart and account/key deletion without retaining credentials or note bodies.

#### Scenario: Child recovers history after rotation
- **GIVEN** accounts A and B have participated in the same root session
- **WHEN** `/root/child` queries history for that session
- **THEN** both accounts receive the original request including agent selectors
- **AND** both complete results are available to the model

#### Scenario: Keepalive-only startup ends
- **WHEN** HTTP streaming receives only SSE comments and then closes or fails
- **THEN** the API-key ownership fence remains and no history participant is added

#### Scenario: Keepalives precede a response
- **WHEN** a parsed upstream response event follows SSE comments or unclassified frames
- **THEN** the selected account is recorded before that event reaches the client
