## MODIFIED Requirements

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
