## ADDED Requirements

### Requirement: Direct-source client metadata preserves portability

Direct HTTP Responses routing MUST recognize `internal_chat_message_metadata_passthrough` containing a nonblank string `turn_id`, optional finite numeric non-boolean `create_time`, and optional `content_item_kinds` list of nonblank strings as client bookkeeping. Ownership extraction and portability classification MUST use the same validated interpretation. This allowance MUST NOT change existing input normalization or forwarding; metadata on forwarded input items MUST remain unchanged. This allowance MUST apply to otherwise supported client messages, complete call/result pairs, client tool results, standalone named outputs and inline agent messages. Unknown metadata keys or malformed values MUST NOT qualify. Other upstream item, response, call, reasoning, compaction and file ownership checks, output publication and subscription replay rules MUST remain unchanged. This behavior MUST hold on `/v1/responses` and `/backend-api/codex/responses`, including trailing slashes and JSON/SSE responses.

#### Scenario: Fresh client conversation in a source pool

- **WHEN** a fresh conversation contains self-contained user and developer messages with local IDs, timestamps and content-kind metadata
- **THEN** a multi-source pool MUST accept the request without requiring ownership of those local IDs
- **AND** upstream MUST receive the same ordered messages and metadata as existing request normalization produces

#### Scenario: New metadata accompanies a retained call on another backend

- **WHEN** a client adds a tool result and message carrying valid client metadata to durably owned call state on another backend
- **THEN** the request MUST reach only the eligible owner
- **AND** an unknown, conflicting, replaced, disabled or disallowed owner MUST still prevent dispatch

#### Scenario: Metadata cannot conceal upstream state

- **WHEN** metadata has extra keys, wrong types, non-finite timestamps, or invalid content-kind values
- **THEN** the new bookkeeping allowance MUST NOT make the input source-neutral
- **AND** valid metadata alongside unknown reasoning, assistant output, file or item-reference state MUST NOT authorize that state

#### Scenario: Subscription replay retains its own contract

- **WHEN** the same input is evaluated for subscription account replay
- **THEN** the direct-source metadata allowance MUST NOT broaden subscription portability
