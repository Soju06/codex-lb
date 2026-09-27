## ADDED Requirements

### Requirement: Self-contained client message IDs do not bind direct-source ownership

For direct-source Responses selection, the proxy MUST treat an ID on a fully self-contained user, system, or developer message as client bookkeeping rather than an upstream reference. The message without its ID MUST pass the existing account-neutral message validation, including rejection of opaque fields and file references. The proxy MUST preserve the original message body when forwarding. This classification MUST apply consistently to reference extraction and portability checks, and MUST NOT apply to assistant messages, item references, reasoning, compaction, or incomplete or malformed messages. Existing source/key/model ownership checks for other request state MUST remain authoritative. Subscription replay classification MUST remain unchanged.

#### Scenario: Fresh Codex request after pool expansion

- **GIVEN** one source accepts a Codex request with self-contained client-generated message IDs
- **WHEN** four additional sources for that public model become eligible
- **THEN** equivalent fresh requests MUST remain eligible for distribution without an ownership error
- **AND** the selected upstream MUST receive the original client message IDs and content

#### Scenario: New user message continues an owned response on another replica

- **GIVEN** a response or encrypted reasoning item has been durably recorded for source A
- **WHEN** another replica receives that state alongside a new self-contained user message ID in a five-source pool
- **THEN** source A MUST receive the request
- **AND** disabling or replacing A or adding conflicting upstream state MUST still reject the continuation

#### Scenario: Reference-like messages remain protected

- **WHEN** a pooled request contains an unknown assistant output ID, an item reference, or a client-role message that fails account-neutral shape validation
- **THEN** the client-message allowance MUST NOT make that request portable or erase its upstream ownership evidence

#### Scenario: Developer-role tool bundle is not a client message

- **WHEN** an `additional_tools` input item contains a developer role and an ID
- **THEN** the client-message allowance MUST NOT erase its item ID from ownership checks
- **AND** an unknown bundle ID MUST remain rejected in a source pool

### Requirement: Direct-source web-search content types are neutral controls

For direct-source Responses requests declaring supported web search, the proxy MUST accept a nonempty `search_content_types` list containing only `text` and `image` as a provider-neutral control. It MUST validate the list before ignoring that field for portability classification and MUST forward it unchanged. Malformed values, unknown content types, other unknown declaration fields, and scoped tool state MUST retain existing rejection. Subscription replay classification MUST remain unchanged.

#### Scenario: Codex web search options survive pooling

- **WHEN** a direct-source request declares web search with `search_content_types: ["text", "image"]` and a boolean `external_web_access`
- **THEN** a source pool MUST accept the otherwise portable request and forward both controls unchanged

#### Scenario: Malformed or scoped web search stays nonportable

- **WHEN** `search_content_types` is empty, not a list, includes a non-string or unknown value, or accompanies an upstream reference field
- **THEN** the allowance MUST NOT classify that request as portable
