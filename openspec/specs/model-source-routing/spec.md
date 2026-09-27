# Model Source Routing Specification

## Purpose

Define capability-based routing and accounting for OpenAI-compatible model sources, including field-preserving embeddings forwarding.

## Requirements

### Requirement: Model sources declare an embeddings capability

Each model source MUST carry a persisted `supports_embeddings` boolean
capability flag. The flag MUST default to disabled, so a source created or
migrated without an explicit value MUST NOT be treated as embeddings-capable.
The model-source create, read, and update contracts MUST expose the flag, and
the stored value MUST survive a round trip through those contracts.

#### Scenario: existing sources default to disabled

- **GIVEN** a model source row that predates the embeddings capability
- **WHEN** the schema migration runs
- **THEN** the source reports `supports_embeddings` as disabled
- **AND** its existing chat-completions, responses, and audio-transcription
  routing is unchanged

#### Scenario: capability round-trips through the API

- **WHEN** a client creates or updates a model source with the embeddings
  capability enabled
- **THEN** reading the source back reports the capability as enabled

#### Scenario: omitted capability parses as disabled

- **WHEN** a model-source payload omits `supports_embeddings`
- **THEN** it parses as disabled rather than failing validation

### Requirement: Embeddings route only to capable model sources

The system SHALL expose `POST /v1/embeddings` and MUST serve it only from an
enabled model source of kind `openai_compatible` that declares the embeddings
capability and has the requested model enabled. Embeddings requests MUST NOT
fall back to subscription-backed accounts. When the caller presents an API key
restricted to a set of sources, selection MUST stay inside that set. Beyond
the validated `model` and `input` fields, the request payload MUST be
forwarded to the source verbatim.

#### Scenario: capable source serves the request

- **GIVEN** an enabled model source declaring the embeddings capability with
  the requested model enabled
- **WHEN** a client posts to `/v1/embeddings`
- **THEN** the proxy forwards the payload to that source's `/embeddings`
  endpoint and returns the upstream JSON response

#### Scenario: no capable source is a model error

- **GIVEN** no enabled model source declares the embeddings capability for
  the requested model
- **WHEN** a client posts to `/v1/embeddings`
- **THEN** the proxy returns 404 with an OpenAI-format error envelope using
  code `model_not_found`
- **AND** the request is not routed to a subscription-backed account

#### Scenario: source-restricted API key cannot escape its set

- **GIVEN** an API key restricted to a set of model sources
- **WHEN** the only embeddings-capable source for the model is outside that
  set
- **THEN** the proxy returns `model_not_found`

### Requirement: Embeddings requests are accounted like other source routes

Embeddings responses MUST be inspected for prompt and total token usage. When
the caller's API key requires usage for settlement and the source response
reports none, the proxy MUST fail closed with `usage_unavailable` rather than
serving unmetered traffic. Every embeddings attempt that is dispatched to a
model source MUST produce a request-log entry, with `success` on a forwarded
response and `error` on a forwarding, usage, or settlement failure. That entry
MUST carry the upstream status code when a source returned an HTTP response,
and MUST record the upstream status as absent when the attempt failed before
any response was received. A request rejected before source selection succeeds
is not a dispatched attempt: it MUST NOT produce a request-log entry, because
no source was contacted and no reservation was consumed.

#### Scenario: missing usage fails closed for a limited key

- **GIVEN** an API key whose reservation requires reported usage
- **WHEN** the model source returns an embeddings response without a usage
  object
- **THEN** the proxy returns an error envelope using code `usage_unavailable`
- **AND** records an error request log

#### Scenario: forwarding error propagates the upstream status

- **WHEN** the model source returns an error status for an embeddings request
- **THEN** the proxy returns an OpenAI-format error envelope with that status
- **AND** records an error request log carrying the upstream status code

#### Scenario: transport failure records an attempt without an upstream status

- **WHEN** the request to the model source fails before any HTTP response is
  received
- **THEN** the proxy records an error request log for the attempt with no
  upstream status code

#### Scenario: unroutable model is not a logged attempt

- **GIVEN** no enabled model source declares the embeddings capability for
  the requested model
- **WHEN** a client posts to `/v1/embeddings`
- **THEN** the proxy returns the `model_not_found` envelope without writing a
  request-log entry
- **AND** no reservation is consumed for the rejected request

### Requirement: Embeddings source forwarding preserves field presence

For source-routed `POST /v1/embeddings` requests, the system MUST preserve both
the values and presence of fields beyond the validated `model` and `input`
fields. A field explicitly supplied as null MUST be forwarded as null, a field
omitted by the client MUST remain absent, and a non-null field MUST be forwarded
unchanged. This forwarding behavior MUST NOT change reservation settlement or
request-log metadata.

#### Scenario: explicit null extras remain present

- **WHEN** a client supplies `dimensions: null` and `user: null` in a
  source-routed embeddings request
- **THEN** the compatible source receives both keys with null values

#### Scenario: omitted extras remain absent

- **WHEN** a client omits `dimensions` and `user` from a source-routed
  embeddings request
- **THEN** the compatible source payload does not contain either key

#### Scenario: non-null extras and accounting remain unchanged

- **WHEN** a client supplies non-null embedding extras through a limited API
  key
- **THEN** the compatible source receives those values unchanged
- **AND** the reservation settles from reported usage
- **AND** the successful request log retains its model-source metadata and
  token counts

### Requirement: Owner-unavailable stream health preserves the recovery cause

The service SHALL use the original upstream error code for account-health
recovery when a Responses stream rewrites an upstream failure to
`previous_response_owner_unavailable`. The rewrite MUST NOT change
source-ownership selection, owner pinning, or stale-anchor matching.

#### Scenario: Owner-unavailable rewrite records original recovery code

- **WHEN** an upstream Responses failure with an account-recovery code is
  rewritten to `previous_response_owner_unavailable`
- **THEN** account health receives the original upstream code
- **AND** source ownership and stale-anchor classification remain unchanged

### Requirement: Source models support explicit upstream aliases

The system SHALL accept an optional `upstream_model` in each source model's `raw_metadata_json`. If present, it MUST be a string containing 1 to 255 characters after trimming. The stored `model` MUST remain the client-visible identity. Absent mapping MUST preserve identity forwarding. Mapping MUST be applied exactly once, after source selection, on supported source Responses, Chat Completions, Embeddings and Audio Transcriptions HTTP requests, including existing equivalent and trailing-slash routes. Mapping MUST NOT alter authorization, model enablement, source assignment, pricing, request-log model, reservation settlement or continuity ownership.

#### Scenario: Client alias maps to an opaque upstream ID

- **GIVEN** public model `cd/gpt-6-astra` with `upstream_model` equal to `cd/linxaq`
- **WHEN** an authorized client requests `cd/gpt-6-astra`
- **THEN** the selected endpoint receives `model` equal to `cd/linxaq`
- **AND** accounting and continuity use `cd/gpt-6-astra`

#### Scenario: Identity and one-hop mapping

- **WHEN** a model has no upstream mapping
- **THEN** its upstream request retains its public model ID
- **AND** an explicitly mapped target is not recursively resolved through other model rows

#### Scenario: Existing route spelling remains authoritative

- **WHEN** a client posts an alias to `/v1/responses/` or `/backend-api/codex/responses/`
- **THEN** it is mapped as on the equivalent route without the trailing slash
- **AND** unsupported trailing-slash Chat Completions, Embeddings and Audio Transcriptions URLs retain their existing 405 error envelope without dispatch

#### Scenario: Invalid mapping is rejected

- **WHEN** a create or update supplies a null, non-string, blank or overlength `upstream_model`
- **THEN** the dashboard API rejects the payload without persisting it

#### Scenario: Alias cannot bypass source or model restrictions

- **WHEN** a key requests an alias outside its allowed models or assigned sources
- **THEN** no request is dispatched to that alias's upstream endpoint

### Requirement: Aliased source responses retain the public model identity

For a mapped source request, the proxy MUST replace an upstream model value matching the configured target in a successful response's top-level `model` or Responses event's `response.model` with the public alias. This MUST apply to JSON and SSE responses. Text, tool arguments, usage, response IDs and other fields MUST remain unchanged. Streams MUST retain cancellation cleanup and bounded buffering. Sources without an alias MUST retain existing response behavior.

#### Scenario: Streamed model fields use the alias

- **WHEN** a mapped source emits a fragmented SSE event with its upstream model ID
- **THEN** the client receives the corresponding model field as the public alias
- **AND** tool arguments and generated text containing the upstream ID remain unchanged

### Requirement: Dashboard model entry supports aliases

The create and edit source forms SHALL accept `alias=upstream-id` entries alongside bare model IDs. Forms MUST reject missing sides, multiple equals separators and duplicate public aliases. Editing a bare existing model into an alias referencing that model MUST preserve its capabilities, pricing, enablement and raw metadata. Unrelated edits MUST preserve existing mappings. Removing the mapping MUST restore identity behavior.

#### Scenario: Rename an existing model for clients

- **WHEN** an operator edits existing `cd/linxaq` into `cd/gpt-6-astra=cd/linxaq`
- **THEN** the saved public model is `cd/gpt-6-astra` with target `cd/linxaq`
- **AND** its existing multi-agent metadata and model settings are retained

### Requirement: Responses balance authorized equivalent model sources

Portable HTTP Responses requests without credential-bound history MUST distribute among enabled Responses-capable sources declaring the exact public model and permitted by the presenting API key. Selection MUST prefer fewer in-flight dispatches and rotate equally loaded sources, exclude saturated or cooling sources, and apply each selected source's own alias and credential. State MUST be bounded and replica-local. Source membership MUST remain distinct from availability so unavailable pools do not fall through to subscription accounts. Single-source requests MUST retain their admission policy and accept externally created state when there is no conflicting ownership evidence.

#### Scenario: Five credentials share one public model

- **GIVEN** five eligible sources with separate credentials and the same public alias
- **WHEN** sequential requests arrive without a previous response anchor
- **THEN** the sources share requests instead of always selecting the first name
- **AND** the client retains one endpoint, codex-lb API key and public model

#### Scenario: Scope and capacity constrain selection

- **WHEN** some matching sources are disabled, lack streaming capability, are outside the key's assignment, saturated or cooling
- **THEN** only available authorized capable sources may be dispatched
- **AND** an unavailable pool returns an OpenAI-format 503 with Retry-After without acquiring a usage reservation

#### Scenario: Declared collaboration tools and neutral controls survive pooling

- **GIVEN** equivalent sources declare the requested collaboration namespace capability
- **WHEN** a fresh request declares collaboration tools or neutral generation controls such as `background: false`, `max_tool_calls`, or `stream_options.include_obfuscation`
- **THEN** adding a second eligible source MUST NOT turn that supported request into an ownership error
- **AND** reference-free supported namespace tools MUST reach the selected upstream unchanged
- **AND** replay eligibility MUST remain distinct from supported initial dispatch and MUST NOT authorize cross-credential replay of opaque state

### Requirement: Source failover owns and settles each attempt

Before returning a Responses stream or non-stream response, eligible portable requests MAY retry explicit upstream 401/403, 429 or 5xx rejections and proven connection failures on another eligible source. Retry MUST visit at most five distinct sources, recheck candidate availability, finalize the prior reservation and release its admission before updating cooldown or creating another reservation, and log each dispatched attempt. Exhaustion MUST preserve the final upstream error envelope and Retry-After. Credential, rate and transient failures SHALL temporarily cool the failed source with a bounded Retry-After policy. Replacing a source's key or endpoint MUST clear stale cooldown. Single-source source-error passthrough MUST remain unchanged except for the explicit redirect and ownership persistence requirements.

#### Scenario: Rejected credential advances after cleanup

- **WHEN** a source rejects credentials and another authorized source succeeds
- **THEN** the first attempt's reservation and slot are released before the second is reserved
- **AND** both source attempts are recorded and subsequent requests skip the cooling source

#### Scenario: Unsafe replay is declined

- **WHEN** a request is anchored, a stream has been returned, the client disconnects, a header/body timeout occurs, a successful upstream body is malformed or reservation finalization fails
- **THEN** the request is not replayed on another credential
- **AND** client cancellation and idle disconnects do not penalize otherwise healthy sources

### Requirement: Source response continuity preserves credential ownership

Recorded source response anchors MUST resolve within the presenting API key and public model scope and remain on the recorded source. A known owner that is disabled, removed, disallowed, saturated or cooling MUST NOT cause a switch to another source. Conflicting ownership or missing ownership in a multi-source pool MUST fail closed. Existing subscription-owned and file-pinned routing MUST remain authoritative. Ownership lookup failures MUST fail closed without dispatch.

#### Scenario: Resume on a different replica

- **GIVEN** a completed source response is recorded in shared request logs
- **WHEN** the client continues with its response ID on another replica
- **THEN** the same eligible source handles the request regardless of rotation state

#### Scenario: Unknown or inaccessible owner

- **WHEN** an anchor has no unambiguous permitted owner in a pool or its recorded owner is no longer eligible
- **THEN** the response reports unavailable continuity without contacting a different source

#### Scenario: File and subscription ownership wins

- **WHEN** the payload references an uploaded file or a subscription-owned response
- **THEN** the existing account ownership path handles the request and source pooling does not bypass its restrictions

#### Scenario: Credential-bound history cannot rotate

- **WHEN** a request includes conversation state, encrypted reasoning, item references or other non-portable state
- **THEN** a multi-source pool MUST resolve unambiguous ownership or decline without dispatch
- **AND** the request MUST NOT fail over to another credential
- **AND** conflicting references and credential replacement MUST fail closed

#### Scenario: Known source owner has no eligible candidate

- **WHEN** a known source-owned request has no matching eligible source because sources were removed or their capability no longer matches
- **THEN** it MUST fail closed before subscription dispatch
- **AND** lookup MUST retain the original public-model scope when request normalization changes its spelling
- **AND** authoritative file and subscription ownership handling MUST retain its existing precedence

#### Scenario: Saving an identical upstream token

- **WHEN** an operator saves exactly the currently configured upstream token without changing its endpoint or upstream model
- **THEN** existing response ownership MUST remain usable on the same source across backends
- **AND** replacing the token with a different credential MUST still invalidate incompatible continuity

#### Scenario: Overrides introduce different continuity references

- **WHEN** source request overrides change reference-bearing input, conversation or previous-response state
- **THEN** conflicting or unknown ownership in the effective forwarded payload MUST be rejected before upstream dispatch
- **AND** each effective reference MUST be individually resolved; a known response owner MUST NOT authorize another unresolved response ID
- **AND** ownership SHALL be evaluated separately for each candidate; references introduced only by another candidate MUST NOT block a valid owner
- **AND** a post-generation publication failure MUST NOT be the first ownership check for those references

#### Scenario: Unresolved tool outputs refer to another owner

- **WHEN** a request combines an anchor owned by one source with an unresolved tool result whose `call_id` belongs to another source
- **THEN** it MUST fail closed without dispatch
- **AND** only complete, ordered, type-matched call/result pairs without opaque upstream state SHALL bypass reference ownership; partial calls and mismatched results MUST retain their ownership checks
- **AND** a complete pair retaining its returned bookkeeping item IDs SHALL use the same portability classification without changing the forwarded body

### Requirement: Direct source ownership is durable before delivery

Before exposing a source response identifier, output item reference, encrypted output or successful completion, the proxy MUST durably record its owning source within the presenting API-key and public-model scope. It MUST store fingerprints rather than raw encrypted content. Persistence failure MUST withhold the corresponding successful output and finalize the dispatch without replay. Ownership MUST be available across replicas before accounting logs finish. Active reference records SHALL expire after 30 days without refresh and SHALL be purged by retention cleanup. Expiration MUST NOT authorize continuity on a replacement credential. Existing response logs SHALL remain usable for unambiguous historical response-ID ownership only when compatibility evidence can establish the required owner and credential continuity; insufficient evidence MUST fail closed. Single-source requests SHALL retain legacy acceptance of externally created state and record successful state for later pool use.

#### Scenario: Immediate continuation on another replica

- **WHEN** a client continues a delivered response before the preceding request-log write finishes
- **THEN** the new request resolves the same source and does not fail merely because that log is pending

#### Scenario: Encrypted output remains on its credential

- **WHEN** a response generated by one source is replayed with encrypted reasoning on another replica
- **THEN** its recorded source receives the request even when another source has less load
- **AND** an unavailable or changed owner does not cause cross-credential replay

#### Scenario: Ownership storage fails

- **WHEN** durable ownership cannot be recorded
- **THEN** the affected successful output is withheld and a source ownership error is returned
- **AND** quota/admission cleanup completes without trying another source

#### Scenario: Output references appear before completion

- **WHEN** a stream exposes an item ID in a delta or content frame before completion
- **THEN** ownership MUST be durable before that frame is delivered, including frames synthesized by the public normalizer
- **AND** replayed output IDs on messages and tool calls MUST resolve ownership and detect conflicting references

#### Scenario: Cancellation after completed JSON generation

- **WHEN** a JSON request is cancelled while publishing ownership after upstream usage has been captured
- **THEN** cleanup MUST settle that usage under the existing cancellation policy, release admission and await the ownership write without replay

#### Scenario: Retention races with another backend

- **WHEN** one backend renews an expired ownership record while another backend is pruning it
- **THEN** a renewal committed before deletion MUST preserve the now-live record

#### Scenario: Historical ownership conflicts with a new publication

- **GIVEN** a response ID has unambiguous historical ownership on source A
- **WHEN** source B attempts to publish the same scoped response ID
- **THEN** B's publication MUST fail before exposing the conflicting reference
- **AND** A's historical ownership MUST NOT be replaced or shadowed by B
- **AND** concurrent publications on separate backends MUST uphold the same invariant

#### Scenario: Expired ownership and replaced credential

- **WHEN** an ownership reference expires, its accounting log remains, and its source credential changes
- **THEN** continuing the old reference MUST fail before upstream dispatch
- **AND** this MUST hold both before and after retention removes the expired active reference

#### Scenario: Legacy logs do not establish credential continuity

- **WHEN** a historical response exists only in request logs without a recorded source credential revision
- **THEN** continuation and a new conflicting publication MUST fail closed rather than assume the currently configured token owns it
- **AND** authoritative durable ownership MUST take precedence over failed competing publication logs

#### Scenario: Additive ownership history migration

- **WHEN** the history migration runs on an existing database
- **THEN** it MUST preserve the source and credential revision of both live and expired ownership rows
- **AND** existing request logs MUST remain intact with an unknown credential revision rather than a fabricated current revision
- **AND** retention of active ownership rows MUST NOT remove historical credential evidence

#### Scenario: Pruning does not erase the credential fence

- **WHEN** an expired ownership row is pruned and a response anchor is later presented
- **THEN** durable history MUST still identify its original source and credential revision
- **AND** a replaced credential MUST be rejected before upstream dispatch
- **AND** conflicting failed request logs MUST NOT override a durable owner

#### Scenario: Legacy response log has no credential revision

- **WHEN** historical source ownership exists only in a request log without a recorded credential revision
- **THEN** the proxy MUST treat credential continuity as unverifiable and fail closed
- **AND** it MUST NOT infer that the current source credential generated the response

### Requirement: Responses redirects do not authorize replay

Responses forwarding MUST NOT automatically follow upstream redirects. A redirect SHALL produce a non-retryable 502 source redirect error without forwarding Location to the client, regardless of streaming mode. Other source protocol routes SHALL retain their existing redirect behavior.

#### Scenario: Accepted POST redirects to an unreachable result

- **WHEN** a source receives a Responses POST and redirects to an unreachable result URL
- **THEN** no redirected request or second generation POST is sent
- **AND** the original attempt releases its quota reservation and admission

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

### Requirement: Prompt template references retain source ownership

For direct-source Responses requests, the proxy MUST resolve `prompt.id` in the presenting API-key and exact public-model scope. Successful source requests MUST durably publish their prompt reference before delivering completion, including streaming completion, so another backend can resolve it. A prompt unknown to a pool or conflicting with another reference MUST be rejected before dispatch. Credential replacement MUST invalidate recorded prompt continuity, including after active ownership expiry. Original external prompts MUST retain single-source compatibility when no conflict exists. Unknown prompt IDs introduced by source overrides MUST be rejected even for one source.

#### Scenario: Prompt conflicts with an anchor

- **WHEN** a request combines source A's response anchor with a prompt recorded for B or an unknown prompt
- **THEN** neither source MUST receive the request

#### Scenario: Prompt learned before expanding a pool

- **WHEN** a successful single-source request used an external prompt and a second source is later assigned
- **THEN** another backend MUST continue that prompt on its recorded owner
- **AND** replacing that owner's credential MUST reject the old prompt before dispatch

#### Scenario: Prompt supplied by source overrides

- **WHEN** a source override supplies a prompt ID
- **THEN** that candidate MUST be eligible only when that ID resolves to its current credential
- **AND** an invalid candidate MUST NOT block another valid candidate

### Requirement: Prompt variable files cannot enter direct sources

An effective source request with a file reference in an `input_file` or `input_image` value of `prompt.variables` MUST be rejected before source admission, quota reservation, or upstream dispatch. This MUST apply to client-supplied and override-supplied variables with one or multiple sources, regardless of a matching prompt or response owner. The proxy MUST NOT redirect override-introduced files to a subscription account. Reference-free text, inline data, and ordinary URL variables MUST retain their existing forwarding behavior. Existing original input-file subscription routing MUST remain unchanged.

#### Scenario: Prompt variable refers to an account file

- **WHEN** a request with a matching source anchor and prompt includes a file ID in a prompt variable
- **THEN** the source MUST NOT receive it and no source quota reservation MUST be created

#### Scenario: Override introduces file-bearing variables

- **WHEN** one candidate's override inserts a file-bearing prompt variable
- **THEN** that candidate MUST be rejected without blocking a safe candidate

#### Scenario: Variables contain reference-free content

- **WHEN** an otherwise eligible request supplies ordinary text, inline file data, or an ordinary image URL as prompt variables
- **THEN** those values MUST reach the selected source unchanged

### Requirement: Original source state is distinct from override state

Reference validation MUST treat client state retained in the direct-source forwarding body as original request state even when subscription-specific cleanup would remove it. A single source with no conflicting ownership MUST accept an original external compaction item following a recognized local compact fallback marker. The proxy MUST still reject unknown override-introduced state and conflicting original state, including on source lookup misses and model normalization.

#### Scenario: Existing compacted history with one source

- **WHEN** an original request contains the local compact fallback marker followed by an external encrypted compaction item and has one eligible source
- **THEN** the request MUST retain single-source external-state compatibility without being rejected as an unknown override

#### Scenario: Compacted history belongs to an unavailable owner

- **WHEN** an original retained compaction reference has a known unavailable or conflicting owner
- **THEN** subscription-specific cleanup MUST NOT erase the evidence and authorize another source or account

### Requirement: Log-probability controls remain source-neutral

A direct-source Responses request with integer `top_logprobs` from 0 through 20 MUST remain eligible for initial source pooling and portable failover when its other state is portable. The value and log-probability include fields MUST be forwarded unchanged on both HTTP routes and streaming requests. Booleans, out-of-range numbers, and other malformed values MUST NOT be classified as neutral by this allowance. Subscription-overflow replay policy MUST remain unchanged.

#### Scenario: A second source is added

- **WHEN** a fresh request with valid `top_logprobs` succeeds with one source and another equivalent source is assigned
- **THEN** the same request MUST remain accepted, including from a streaming SDK client

#### Scenario: Invalid log-probability control

- **WHEN** a pooled request supplies a boolean, out-of-range number, or malformed `top_logprobs` value
- **THEN** this direct-source allowance MUST NOT authorize its dispatch

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
