## MODIFIED Requirements

### Requirement: Private context transport
The proxy MUST NOT include context contents, private upstream error text, or credential-bearing header values in control-request diagnostics or upstream payload tracing. Native tool arguments and encrypted outputs MUST remain opaque. Context request logs SHALL contain generic operation status without payload content. Context endpoint failures SHALL use the generic OpenAI error code `context_backend_unavailable`, preserving a direct upstream HTTP failure status. Typed upstream and validation failures SHALL retain their HTTP status for both single-account and multi-account history. Unexpected transport failures SHALL return a generic HTTP 503 response. Failed history operations MUST NOT return partial results.

#### Scenario: Upstream echoes sensitive content in an error
- **WHEN** an upstream failure contains private note text
- **THEN** the downstream error and proxy diagnostics do not contain that text

#### Scenario: Encryption metadata passes unchanged
- **WHEN** Codex includes `x-openai-encrypted-tool-arguments` and `x-openai-tool-output-truncation-policy`
- **THEN** the proxy forwards both headers without decoding or reserializing the request body


### Requirement: Durable history participation
Authenticated Responses with `reasoning.context=all_turns` and a canonical `client_metadata.session_id` SHALL bind session/API-key ownership before HTTP, HTTP-bridge or native WebSocket dispatch. HTTP streaming SHALL record participation after its first upstream event; startup failures or empty streams before that event MUST NOT add a participant. HTTP-bridge and native WebSocket participation SHALL persist before send. A rejected attempt after these observation boundaries MAY leave an empty history participant. Native WebSocket requests MUST set their sent timestamp immediately before send, without an intervening await. Context calls MUST validate the root session UUID and current agent path and preserve child-agent query fields. History queries SHALL contact every recorded participant, or the notes owner when none exists, within the current key scope. Ownership and participant records MUST survive process restart and account/key deletion without retaining credentials or note bodies.

#### Scenario: Child recovers history after rotation
- **GIVEN** accounts A and B have participated in the same root session
- **WHEN** `/root/child` queries history for that session
- **THEN** both accounts receive the original request including agent selectors
- **AND** both complete results are available to the model


## ADDED Requirements

### Requirement: Migration owns only newly created context tables
The context ownership migration MUST reject unexpected pre-existing context tables before creating either table. A rejected upgrade MUST preserve their rows and the previous Alembic revision. A successful upgrade SHALL support an idempotent upgrade-to-head and a downgrade/re-upgrade round trip.

#### Scenario: One context table already exists
- **GIVEN** either context table exists before the context ownership revision
- **WHEN** the revision is applied
- **THEN** it fails before creating the other table or changing the existing rows
- **AND** it does not claim the revision
