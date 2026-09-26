## ADDED Requirements

### Requirement: Source reference validation retains original request scope

For Responses requests selecting a source through public-model normalization, the proxy MUST check source-owned references in the original effective public-model scope before dispatch. Known ownership in that scope MUST NOT be erased by normalization or by single-source acceptance of otherwise external state. References genuinely owned in the selected fallback model scope MUST remain usable when there is no conflicting original-scope evidence. The checks MUST apply to both `/v1/responses` and `/backend-api/codex/responses` while preserving file and subscription ownership precedence.

#### Scenario: Original source is removed after a normalized response

- **GIVEN** a source produces a response for the original public model and another source serves a normalized fallback model
- **WHEN** the original source is removed and the client resumes using its original public model and response ID
- **THEN** the proxy MUST return an ownership error without contacting the fallback source

#### Scenario: Fallback-owned response remains usable

- **GIVEN** a request using the original public model was previously served by the normalized fallback source
- **WHEN** the same client continues that response after the fallback source is selected again
- **THEN** the proxy MUST allow the continuation on that same source

#### Scenario: Original owner loses eligibility

- **WHEN** an original-scope reference owner becomes disabled or loses the required Responses capability
- **THEN** a normalized fallback source MUST NOT receive that reference

### Requirement: MCP approval responses preserve item ownership

An `mcp_approval_response.approval_request_id` MUST resolve against the ownership of the referenced `mcp_approval_request.id`. Unknown approval references in a pool and approval references that conflict with another request reference MUST fail before source dispatch. An approval response and anchor owned by the same eligible source MUST continue on that source. Complete portable tool call and result pairs MUST retain their existing treatment.

#### Scenario: Mixed owners

- **WHEN** a response anchor belongs to source A and an approval response refers to an approval request item from source B
- **THEN** the proxy MUST return an ownership error without forwarding the approval response

#### Scenario: Unknown approval item

- **WHEN** a pooled request refers to an approval request item with no ownership evidence
- **THEN** the proxy MUST return an ownership error without contacting a source

#### Scenario: Same owner approval

- **WHEN** an approval response and its response anchor belong to one eligible source
- **THEN** that source MUST receive the continuation

### Requirement: Object-form conversation overrides are validated

When a source request override introduces `conversation: {"id": "..."}`, the proxy MUST validate its ID as a conversation reference before dispatch, including for a single eligible source. An unknown override-introduced ID, malformed override conversation, or ownership conflicting with another request reference MUST be rejected. A malformed override on one candidate MUST NOT block another valid candidate. A known ID owned by the selected source MUST be accepted. A single-source client request containing external conversation state MUST retain its existing compatibility when no conflicting evidence exists.

#### Scenario: Unknown override in one-source configuration

- **WHEN** a source override introduces an unknown object-form conversation ID
- **THEN** the proxy MUST reject the request before contacting that source

#### Scenario: Known and conflicting overrides

- **WHEN** an override introduces a known conversation ID
- **THEN** the owner source MUST remain eligible and a different source MUST NOT receive it

#### Scenario: Original external conversation

- **WHEN** an original client request supplies an external conversation ID to a single eligible source without conflicting ownership evidence
- **THEN** the proxy MUST preserve single-source compatibility

#### Scenario: Malformed override conversation

- **WHEN** an override supplies a conversation object without a nonempty string ID or another malformed conversation value
- **THEN** the proxy MUST reject that source before dispatch
- **AND** another valid candidate MUST remain eligible

### Requirement: Effective source requests honor subscription file pins

After source request overrides, a Responses source candidate with an `input_file` or `input_image` file ID in its effective forwarded input, or a `code_interpreter.container.file_ids` entry in its effective tools, MUST be rejected before source admission, quota reservation, or upstream dispatch. The proxy MUST NOT reroute override-introduced file state to a subscription account. An original client request with an input file ID MUST retain the existing subscription-account route and pin precedence.

#### Scenario: Override inserts a pinned file

- **WHEN** a source override inserts a file ID that belongs to a subscription account into an otherwise source-routed request
- **THEN** that source MUST NOT receive the request and the request MUST fail before source admission or reservation when no safe candidate remains

#### Scenario: Original client file reference

- **WHEN** the original client input supplies an `input_file` or `input_image` file ID
- **THEN** existing subscription-account routing MUST handle it without source dispatch

#### Scenario: Hosted tool declares a file ID

- **WHEN** a direct source request declares `code_interpreter.container.file_ids` with a subscription-pinned file ID
- **THEN** the source MUST NOT receive it and the request MUST fail before source admission or reservation when no safe candidate remains

#### Scenario: Malformed source override input

- **WHEN** one source override gives an input item or its content/output part a nonstring `type`
- **THEN** that source MUST be rejected before file-reference extraction without blocking another valid candidate
- **AND** a request with only malformed candidates MUST fail before dispatch

### Requirement: Code-interpreter container references retain source ownership

The proxy MUST record container IDs exposed by source code-interpreter output and MUST resolve container IDs referenced by code-interpreter tool declarations or retained input items in the same API-key and public-model scope. Conflicting or unknown container ownership in a pool MUST fail before dispatch; a known matching owner MUST remain usable. An auto-container declaration MUST NOT be treated as a container ID; existing hosted-tool pool portability policy remains unchanged.

#### Scenario: Container belongs to another source

- **WHEN** a request anchored to source A refers to a container ID published by source B
- **THEN** the proxy MUST reject the request without contacting either source

#### Scenario: Unknown container and matching container

- **WHEN** a pooled request references an unknown container ID
- **THEN** the proxy MUST reject it before dispatch
- **AND** a request referencing a container published by its anchored eligible source MUST continue on that source

#### Scenario: Container reference in retained input

- **WHEN** a retained code-interpreter input item contains a container ID owned by another source
- **THEN** the proxy MUST reject it before dispatch

### Requirement: File-search vector stores retain source ownership

The proxy MUST resolve each string ID in a declared `file_search.vector_store_ids` against source ownership recorded from successful source requests in the same client-key and public-model scope. Unknown vector-store IDs in a pool and IDs conflicting with an anchored source MUST fail before dispatch. A matching owner MUST remain eligible. Original single-source external state MUST retain its existing compatibility when no conflicting evidence exists; override-introduced unknown IDs MUST be denied before dispatch.

#### Scenario: Vector store belongs to another source

- **WHEN** a request anchored to source A declares a vector-store ID published by source B
- **THEN** the proxy MUST reject it without dispatch

#### Scenario: Unknown and matching vector stores

- **WHEN** a pooled request declares an unknown vector-store ID
- **THEN** the proxy MUST reject it before dispatch
- **AND** a declared ID published by the anchored eligible source MUST continue on that source

### Requirement: Declared fresh direct-source tools remain compatible

For a source that declares the corresponding tool support, the proxy MUST accept a fresh, reference-free `namespace` declaration with a nonblank name and valid nested function declarations, and a fresh `web_search` declaration with a boolean `external_web_access`. The selected source MUST receive the original declarations unchanged. This direct-source classification MUST NOT loosen subscription-overflow replay eligibility or permit opaque references to cross sources.

#### Scenario: Custom namespace in a pool

- **WHEN** a fresh request declares a valid named `namespace` tool supported by each eligible source
- **THEN** the request MUST reach a selected source with the declaration unchanged

#### Scenario: Web-search access option in a pool

- **WHEN** a fresh request declares a supported `web_search` tool with boolean `external_web_access`
- **THEN** the request MUST reach a selected source with that option unchanged

### Requirement: Empty stream options do not imply upstream ownership

A direct source Responses request with `stream_options: {}` MUST remain eligible for source pooling when its other state is portable. The empty object MUST be forwarded unchanged. This allowance MUST NOT change subscription-overflow replay eligibility.

#### Scenario: Empty options in equivalent-source pool

- **WHEN** a fresh Responses request has `stream_options: {}` and two eligible equivalent sources
- **THEN** one source MUST receive it without an ownership error

### Requirement: Failed JSON ownership publication retains upstream observations

When a source returns a successful JSON Responses body but durable ownership publication fails, the proxy MUST withhold the body, return an ownership error, release the usage reservation and admission, and write an error request log containing the observed upstream HTTP status, usage and timings. It MUST NOT retry on another source or charge the client for the withheld response.

#### Scenario: Response identifier collision after upstream success

- **WHEN** a second source returns a JSON response ID already owned by another source in the same scope
- **THEN** the client MUST receive an ownership error without the conflicting successful body
- **AND** the error log MUST retain the second source's upstream status, usage and timings
- **AND** its usage reservation MUST be released without another source attempt
