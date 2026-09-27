## ADDED Requirements

### Requirement: Standalone named function outputs are direct-source neutral

Direct HTTP Responses routing MUST accept a standalone `function_call_output` as source-neutral when it has no `call_id` field, has a nonblank `name`, contains self-contained string or supported inline tool-result content, and has only the fields `type`, `id`, `name`, `namespace`, `output`, and `internal_chat_message_metadata_passthrough`. An optional ID MUST be nonblank, an optional namespace MUST be a string, and optional internal metadata MUST pass existing account-neutral metadata validation. Opaque state, file references, unknown fields and malformed values MUST NOT qualify. The same validation MUST govern ownership extraction and direct-source portability. This classification MUST NOT rewrite the forwarding body or change the notification's fields, content or position among retained input items on `/v1/responses` and `/backend-api/codex/responses` and their trailing-slash variants, for streaming and non-streaming requests. Subscription replay policy and upstream ownership publication MUST remain unchanged.

#### Scenario: Subtask starts after pool expansion

- **GIVEN** a client subtask contains self-contained messages and a named standalone function output
- **WHEN** its public model expands from one source to five and a different backend receives the request
- **THEN** the request MUST remain eligible without requiring ownership of the standalone output's client ID
- **AND** the selected source MUST receive the original ordered input

#### Scenario: Retained state still determines the owner

- **WHEN** a valid standalone output accompanies durably owned response or call state
- **THEN** the request MUST go only to that eligible owner
- **AND** unavailable, replaced, disallowed, differently scoped or conflicting ownership MUST still reject dispatch

#### Scenario: Named output cannot hide a call reference

- **WHEN** a named function output has a `call_id` field or carries malformed or opaque state
- **THEN** the standalone allowance MUST NOT erase its ownership evidence or make it portable

#### Scenario: Local output ID is later used as a reference

- **WHEN** a standalone client output ID is submitted as an `item_reference`
- **THEN** the ordinary upstream item ownership checks MUST apply
