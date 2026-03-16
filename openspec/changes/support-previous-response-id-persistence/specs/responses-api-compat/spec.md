## MODIFIED Requirements

### Requirement: Support Responses input types and conversation constraints
The service MUST accept `input` as either a string or an array of input items. When `input` is a string, the service MUST normalize it into a single user input item with `input_text` content before forwarding upstream. When a client supplies `previous_response_id`, the service MUST resolve that id from proxy-managed durable response snapshots scoped to the current requester, rebuild the prior conversation input/output history as explicit upstream input items, and continue to reject requests that include both `conversation` and `previous_response_id`.

#### Scenario: previous response id resolves to replayable history
- **WHEN** the client sends `previous_response_id` that matches a persisted prior response snapshot
- **THEN** the proxy rebuilds the prior chain as upstream `input` items before appending the current request input
- **AND** the current request's `instructions` remain the only top-level instructions forwarded upstream

#### Scenario: unknown previous response id
- **WHEN** the client sends `previous_response_id` that does not match a persisted prior response snapshot for the current requester
- **THEN** the service returns a 400 OpenAI-format error envelope with `param=previous_response_id`

#### Scenario: previous response id belongs to a different API key
- **WHEN** the client sends `previous_response_id` that matches a persisted prior response snapshot for a different API key
- **THEN** the service returns a 400 OpenAI-format error envelope with `param=previous_response_id`
- **AND** the message remains `Unknown previous_response_id`

#### Scenario: conversation and previous response id conflict
- **WHEN** the client provides both `conversation` and `previous_response_id`
- **THEN** the service returns a 4xx response with an OpenAI error envelope indicating invalid parameters

### Requirement: Previous-response replay preserves routing continuity
When a request resolves `previous_response_id`, the service MUST prefer the account that served the referenced response if that account is still eligible for the current request. If the stored account is unavailable, the service MUST fall back to the existing account-selection flow instead of failing solely because the preferred account cannot serve the request.

#### Scenario: preferred prior account still eligible
- **WHEN** the client sends `previous_response_id` that resolves to a snapshot whose account can still serve the current request
- **THEN** the proxy routes the request to that same account
