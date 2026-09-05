# codex-context-management Specification

## Purpose
Support experimental native Codex history and notes across an authenticated account pool with durable ownership, complete history partitions and private transport.

## Requirements

### Requirement: Explicit context backend endpoints
The proxy SHALL accept POST under `/backend-api/codex/alpha/history/v2/` for `list_windows`, `list_items`, `read_item`, and `search_contents`, and under `/backend-api/codex/alpha/notes/v2/` for `thread_hint`, `list_files_by_prefix`, `read_file`, `search_contents`, `append_to_file`, and `write_file`. It MUST NOT provide a wildcard upstream path relay. A single trailing slash SHALL accept the same POST method and body.

#### Scenario: Supported operation reaches the backend
- **WHEN** an authorized client sends a supported operation
- **THEN** the proxy forwards the unchanged method, body, query parameters and encryption/truncation headers to the corresponding upstream Codex path
- **AND** it returns native tool results inside an authenticated context container, except the native `thread_hint` response which remains unchanged

### Requirement: Durable pool context scope
Context endpoints MUST authenticate a valid proxy API key. Global API-key authentication MUST be enabled so Responses and context calls have the same authenticated identity. Unscoped, single-account and multi-account keys SHALL be supported. Each canonical root session UUID MUST bind to exactly one API key and one notes owner. That binding MUST persist before the first context write or observed inference dispatch and MUST NOT change after rotation, restart or concurrent requests. A different key using a bound session MUST receive HTTP 403 without upstream dispatch or account-health penalties. Removing access to a required account MUST fail closed.

#### Scenario: A pool key rotates inference
- **GIVEN** a key with access to accounts A and B and a session whose notes owner is A
- **WHEN** subsequent inference uses B
- **THEN** notes operations continue to use A
- **AND** the session remains assigned to the same API key

#### Scenario: Unauthenticated Responses cannot be tracked
- **GIVEN** global API-key authentication is disabled
- **WHEN** a context endpoint is called with a valid key
- **THEN** it returns HTTP 409 before contacting an upstream account

#### Scenario: Assigned owner is unavailable
- **GIVEN** a notes owner A and another healthy account B
- **WHEN** A is deleted, paused, deactivated or cannot serve the context operation
- **THEN** the operation fails without replacing A or writing notes to B

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

### Requirement: Bounded history containers
The proxy SHALL encrypt and authenticate each context result container using its persistent encryption key and include its API-key ID, session UUID and source account IDs. It SHALL validate integrity, session, key and current account scope before unfolding native encrypted content and images into a Responses tool output. Multi-account history SHALL include all successful partitions and model instructions to combine, deduplicate and apply the requested global order and limit. The proxy MUST NOT claim deterministic global sorting or pagination of opaque history results.

Each operation SHALL admit at most 32 history accounts and at most four concurrent upstream history calls. Context request and decoded aggregate result sizes SHALL be limited to 2,000,000 bytes. Upstream operations SHALL use a 30-second deadline. Fan-out tasks MUST own separate database sessions and MUST cancel and await siblings on failure. No partial history result SHALL be returned.

#### Scenario: Tampered or cross-key result is replayed
- **WHEN** a tool output contains an invalid context container or one for another key, session or excluded account
- **THEN** Responses rejects it before upstream dispatch with HTTP 400 or 403, or the corresponding WebSocket error

#### Scenario: One history participant fails
- **GIVEN** a query is running on multiple accounts
- **WHEN** one participant fails while a sibling is waiting
- **THEN** the proxy cancels and awaits the sibling and returns a generic failure without partial history

### Requirement: Notes ownership is independent of inference quota
A rate-limited notes owner SHALL remain eligible for its context operations. A successful context operation MUST NOT clear inference quota state or mark inference as successful. An explicit HTTP 401 MAY refresh and retry once on the same owner. Ambiguous note writes, timeouts and other upstream failures MUST NOT be retried or moved to another owner.

#### Scenario: Quota rejection preserves notes
- **GIVEN** notes owner A is rate limited and inference has moved to B
- **WHEN** a notes read or append succeeds on A
- **THEN** A remains rate limited for inference and the notes owner remains A

#### Scenario: Ambiguous append failure
- **WHEN** an append returns HTTP 500 or times out
- **THEN** the proxy makes no duplicate append attempt and does not select another owner

### Requirement: Migration owns only newly created context tables
The context ownership migration MUST reject unexpected pre-existing context tables before creating either table. A rejected upgrade MUST preserve their rows and the previous Alembic revision. A successful upgrade SHALL support an idempotent upgrade-to-head and a downgrade/re-upgrade round trip.

#### Scenario: One context table already exists
- **GIVEN** either context table exists before the context ownership revision
- **WHEN** the revision is applied
- **THEN** it fails before creating the other table or changing the existing rows
- **AND** it does not claim the revision
