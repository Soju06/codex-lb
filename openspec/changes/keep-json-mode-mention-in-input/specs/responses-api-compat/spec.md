## ADDED Requirements

### Requirement: JSON mode keeps a JSON instruction in Responses input

When Responses request normalization hoists `system`/`developer` instruction messages into `instructions` for a request whose `text.format.type` is `json_object`, the service MUST keep each `system` or `developer` message that mentions JSON in `input`, in its original position, with the `developer` role. Other instruction messages MUST still be hoisted. Compact requests and Responses Lite input MUST keep their existing normalization.

#### Scenario: System JSON instruction stays in input
- **WHEN** a Responses request with `text.format.type = "json_object"` has a system message `Reply with a JSON object.` and a user message `Say hello.`
- **THEN** `input` starts with a `developer` message `Reply with a JSON object.`, followed by the user message
- **AND** `instructions` do not contain `Reply with a JSON object.`

#### Scenario: Request without JSON mode
- **WHEN** a Responses request without `text.format.type = "json_object"` has a system message that mentions JSON
- **THEN** that message is hoisted into `instructions`

#### Scenario: Normalizing twice
- **WHEN** a normalized JSON-mode payload is validated again
- **THEN** `input` and `instructions` are unchanged
