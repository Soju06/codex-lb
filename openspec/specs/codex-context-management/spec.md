# codex-context-management Specification

## Purpose
Support experimental native Codex history and notes across an authenticated account pool with durable ownership, complete history partitions and private transport.

## Requirements

### Requirement: Context request logs remain readable in the dashboard
The dashboard request-log response schema SHALL accept `requestKind: "codex_context"` for successful and failed context operations without rejecting other rows or pagination metadata in the same response.

#### Scenario: Inference and context operations share a page
- **WHEN** a request-log response contains normal inference rows and successful or failed context rows
- **THEN** the dashboard accepts the complete page and preserves each row's request kind, status and pagination metadata

### Requirement: Explicit context backend endpoints
The proxy SHALL accept POST under `/backend-api/codex/alpha/history/v2/` for `list_windows`, `list_items`, `read_item`, and `search_contents`, and under `/backend-api/codex/alpha/notes/v2/` for `thread_hint`, `list_files_by_prefix`, `read_file`, `search_contents`, `append_to_file`, and `write_file`. It MUST NOT provide a wildcard upstream path relay. A single trailing slash SHALL accept the same POST method and body.

#### Scenario: Supported operation reaches the backend
- **WHEN** an authorized client sends a supported operation
- **THEN** the proxy forwards the unchanged method, body, query parameters and encryption/truncation headers to the corresponding upstream Codex path
- **AND** it returns native tool results inside an authenticated context container, except the native `thread_hint` response which remains unchanged

### Requirement: Durable pool context scope
Context endpoints MUST authenticate a valid proxy API key. Global API-key authentication MUST be enabled so Responses and context calls have the same authenticated identity. Unscoped, single-account and multi-account keys SHALL be supported. Each canonical root session UUID MUST bind to exactly one API key and one notes owner. That binding MUST persist before the first context write or observed inference dispatch and MUST NOT change after rotation, restart or concurrent requests. A different key using a bound session in a context-marked Responses request, a cache-recognized Responses request or an explicit context operation MUST receive HTTP 403 without upstream dispatch or account-health penalties. Removing access to a required account MUST fail closed.

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

### Requirement: Bounded history containers
The proxy SHALL encrypt and authenticate each context result container using its persistent encryption key and include its API-key ID, session UUID and source account IDs. It SHALL validate integrity, canonical source and target session UUIDs, the issuing API key and current account scope before unfolding native encrypted content and images into a Responses tool output. A result issued to the same key MAY be replayed in another canonical session only when the request retains `reasoning.context=all_turns`, which MUST enforce the target session ownership before dispatch. Such replay grants no authority to read or write the source session, does not copy its notes or participant bindings, and leaves subsequent context operations scoped to their explicit target session. Multi-account history SHALL include all successful partitions and model instructions to combine, deduplicate and apply the requested global order and limit. The proxy MUST NOT claim deterministic global sorting or pagination of opaque history results.

Each operation SHALL admit at most 32 history accounts and at most four concurrent upstream history calls. Context request and decoded aggregate result sizes SHALL be limited to 2,000,000 bytes. Upstream operations SHALL use a 30-second deadline. Fan-out tasks MUST own separate database sessions and MUST cancel and await siblings on failure. No partial history result SHALL be returned.

#### Scenario: Tampered or cross-key result is replayed
- **WHEN** a tool output contains an invalid context container, one for another key or excluded account, or cross-session replay without history-enabled target ownership enforcement
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

### Requirement: Context dispatch avoids work for ordinary requests
Dispatch bookkeeping MUST consume already-parsed identity fields rather than decode the full request frame or serialize the Responses body again. A bounded process-local cache of at most 4096 sessions SHALL store only committed ownership and participation. A request without `reasoning.context=all_turns` whose session is absent from that cache MUST NOT perform context database work. Marked requests on cache misses and explicit context operations MUST verify the durable API-key binding. Cache entries MUST NOT authorize context reads or override current account scope.

Repeated dispatches to a cached participant SHALL perform no context database work. Adding an HTTP participant to a cached binding SHALL perform only the participant insertion after the first classified event. The first HTTP dispatch for a previously unseen marked session SHALL retain a durable pre-dispatch ownership commit and a separate post-event participation commit, without holding a database transaction across upstream I/O.

#### Scenario: Ordinary and repeated requests
- **WHEN** an unmarked uncached request or a repeated request to a cached participant is dispatched
- **THEN** context bookkeeping does not open a database session or write a context row

#### Scenario: Marked session misses the cache
- **GIVEN** a context binding already exists under key A
- **WHEN** key B dispatches a marked request after cache eviction or process restart
- **THEN** durable validation rejects it before upstream dispatch

#### Scenario: Persistence fails
- **WHEN** a context ownership or participation commit fails
- **THEN** the cache does not publish the failed change

### Requirement: Context deadlines and tasks use injected collaborators
Context request deadlines, upstream timeout budgets and elapsed-time measurements SHALL use the proxy's injected clock and scheduler. All history partition tasks and their cleanup SHALL be owned by that scheduler. Timeout, failure or caller cancellation MUST cancel and await unfinished sibling tasks before returning or propagating cancellation.

#### Scenario: Virtual deadline expires during fan-out
- **WHEN** the injected clock reaches the context deadline while history partitions remain pending
- **THEN** the request times out and finishes cleanup of every partition without waiting for the wall clock

### Requirement: Deployed context migration remains upgradeable
The context ownership revision SHALL retain its original parent `20260830_000000_add_quota_warmup_claim_expiry`. A separate merge revision SHALL join that branch with the current upstream migration head. Upgrading an installation stamped at the deployed context revision MUST apply missing upstream revisions and preserve context owners and participants. A fresh upstream installation MUST still create the context tables and reach a single head without manual stamping or table adoption.

#### Scenario: Existing context installation upgrades
- **GIVEN** the deployed context revision has owners and participants and lacks later upstream columns
- **WHEN** it upgrades to the merged head
- **THEN** the upstream columns are added and the context rows remain unchanged

#### Scenario: A fork replays a previous result
- **GIVEN** an authenticated history-enabled request for a new canonical session carries an intact result issued to its API key in another session
- **WHEN** the target session is unbound or belongs to that key and the source accounts remain in scope
- **THEN** Responses forwards the native result and binds the target independently
- **AND** a target owned by another key is rejected before dispatch even after a cache miss
