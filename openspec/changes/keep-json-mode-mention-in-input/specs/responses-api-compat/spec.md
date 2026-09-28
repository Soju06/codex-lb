## ADDED Requirements

### Requirement: JSON mode keeps a JSON mention in Responses input

When Responses request normalization hoists `system`/`developer` instruction messages into `instructions` for a request whose `text.format.type` is `json_object`, the service MUST keep a JSON mention in a `user` input message. If a hoisted message mentioned JSON and no `user` message in the normalized `input` does, the service MUST add the text part `Respond in JSON.` to the start of the first `user` message, or append a `user` message containing only that part when `input` has no `user` message. Compact requests and Responses Lite input MUST NOT receive the note.

#### Scenario: Developer JSON instruction is hoisted
- **WHEN** a Responses request with `text.format.type = "json_object"` has a developer message `Reply with a JSON object.` and a user message `Say hello.`
- **THEN** `instructions` contain `Reply with a JSON object.`
- **AND** the user message content is `Respond in JSON.` followed by `Say hello.`

#### Scenario: A user message already mentions JSON
- **WHEN** a JSON-mode Responses request has a user message that mentions JSON
- **THEN** no note is added

#### Scenario: Request without JSON mode
- **WHEN** a Responses request without `text.format.type = "json_object"` hoists a developer message that mentions JSON
- **THEN** no note is added

#### Scenario: Normalizing twice
- **WHEN** a normalized JSON-mode payload with the note is validated again
- **THEN** the note appears once
