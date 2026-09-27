## ADDED Requirements

### Requirement: Client tool-result IDs retain call ownership

For direct HTTP Responses requests, the system MUST treat a nonblank item ID on a validated client-authored `function_call_output`, `custom_tool_call_output` or `apply_patch_call_output` as bookkeeping rather than a separate upstream reference. The result MUST have a nonblank `call_id`, supported fields, status, caller and self-contained content, and MUST NOT carry opaque or file-owned state. Its `call_id` MUST still resolve within the presenting API-key/public-model scope unless the existing complete ordered call/result pair contract independently proves portability. This behavior MUST apply to `/v1/responses` and `/backend-api/codex/responses`, including trailing-slash variants, and MUST preserve the forwarded result, response ownership publication and subscription replay rules.

#### Scenario: Old conversation resumes after a namespaced tool call

- **GIVEN** one source owns the retained reasoning, assistant output and namespaced function call
- **WHEN** a client adds a tool result with a new local ID and that call's ID in a multi-source pool on another backend
- **THEN** the same source MUST receive the continuation without requiring ownership of the local result ID
- **AND** the client result MUST reach upstream unchanged

#### Scenario: Output-only continuation preserves the call owner

- **WHEN** a client submits a validated tool result with a new local ID and a durably owned call ID without replaying the original call
- **THEN** the system MUST route it only to that eligible call owner
- **AND** a disabled, replaced or disallowed owner MUST NOT cause another credential to receive the result

#### Scenario: Local result ID cannot authorize other state

- **WHEN** a result names an unknown call, conflicts with another reference's owner, carries opaque state or has an unsupported shape
- **THEN** the bookkeeping exception MUST NOT permit unsafe multi-source dispatch
- **AND** a known response anchor MUST NOT authorize an unknown result call ID

#### Scenario: Client result ID is not upstream publication

- **WHEN** a local result ID is used as an `item_reference` rather than a validated client result
- **THEN** ordinary item ownership checks MUST apply
- **AND** upstream output IDs MUST continue to be recorded before client delivery
