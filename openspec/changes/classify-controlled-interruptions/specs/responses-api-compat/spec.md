## ADDED Requirements

### Requirement: Controlled upstream interruptions are cancelled terminals

For direct Responses WebSocket requests and HTTP bridge requests using the shared WebSocket terminal handler, `response.incomplete` with `incomplete_details.reason` equal to `interrupted` MUST be recorded as status `cancelled`. The proxy MUST preserve the reason, partial usage, response identity, and downstream terminal event. It MUST NOT mark the account successful or penalize the account for this terminal. Other incomplete reasons and failed events MUST retain their existing error classification.

#### Scenario: A controlled interruption preserves usage without an error

- **WHEN** an upstream response ends with `response.incomplete` and reason `interrupted`
- **THEN** the log has status `cancelled` and error code `interrupted`
- **AND** partial token usage and the downstream incomplete event are preserved

#### Scenario: Output limits remain errors

- **WHEN** an upstream response ends with `response.incomplete` and reason `max_output_tokens`
- **THEN** the log remains status `error` with that reason
