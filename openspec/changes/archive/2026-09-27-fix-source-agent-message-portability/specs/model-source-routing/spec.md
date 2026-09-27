## ADDED Requirements

### Requirement: Inline agent messages are direct-source portable content

Direct HTTP Responses routing MUST classify an inline `agent_message` as source-neutral when it has nonblank string `author` and `recipient`, a nonempty list of content parts, and only `type`, `id`, `author`, `recipient`, `content`, and `internal_chat_message_metadata_passthrough` fields. Each part MUST contain exactly `type: input_text` with string `text`, or `type: encrypted_content` with nonblank string `encrypted_content`. An optional item ID MUST be nonblank, and optional internal metadata MUST pass existing account-neutral metadata validation. The inline encrypted agent payload and local message ID MUST NOT require upstream ownership. This allowance MUST NOT authorize any other encrypted item shape, upstream reference, file state, unknown field, or malformed value. Ownership extraction and direct-source portability MUST use the same validation and preserve the entire original forwarding body. It MUST apply to `/v1/responses` and `/backend-api/codex/responses`, including trailing slashes, JSON and SSE. Subscription replay policy and output-reference publication MUST remain unchanged.

#### Scenario: Encrypted task starts in an expanded source pool

- **GIVEN** a Codex client constructs an inline agent message with text and encrypted content from a parent task
- **WHEN** five sources serve its custom model and another backend receives the new subtask
- **THEN** the request MUST remain eligible without prior ownership of its local message ID
- **AND** the selected source MUST receive all message content unchanged and in order

#### Scenario: Accompanying state still binds the source

- **WHEN** an inline agent message accompanies retained response, reasoning, compaction or tool-call state
- **THEN** that state MUST still select its permitted owner or reject dispatch when unknown, conflicting, disabled, replaced or outside the presenting key and model scope

#### Scenario: Agent-shaped opaque state remains protected

- **WHEN** an agent message contains extra fields, file references, invalid content, or encrypted state outside the supported content part
- **THEN** the agent-message allowance MUST NOT erase its ownership evidence or make the request portable

#### Scenario: Message ID is submitted as a reference

- **WHEN** the same client ID is submitted as an `item_reference`
- **THEN** ordinary upstream ownership checks MUST apply
