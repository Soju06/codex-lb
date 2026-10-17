## MODIFIED Requirements

### Requirement: Verified full resend can recover from selection-time owner loss

An HTTP bridge request MAY move from an unavailable continuity owner to another account only after a typed pre-visible `continuity_owner_unavailable` account-selection result, which the HTTP bridge maps to `previous_response_owner_unavailable`, and positive durable proof that the request contains the complete retained input history. A missing durable owner is not a selector result and MUST fail closed without replay. The durable row MUST provide a positive input-item count and full fingerprint, and the corresponding raw prefix of the incoming list-shaped input MUST match both before any projection occurs.

After the raw prefix proof, the service MUST construct a deterministic plaintext projection by omitting `reasoning`, `web_search_call`, `tool_search_call`, and `tool_search_output` items and removing upstream `id` fields from every retained input item. Retained `internal_chat_message_metadata_passthrough` MUST contain only a nonblank string `turn_id` when present. The projected suffix after the projected prefix MUST contain a completed assistant `output_text` or `refusal` boundary with nonblank content followed by nonblank fresh text, valid fresh file/image input, or a matching delayed asynchronous tool output. The suffix MAY contain multiple intervening turns only when every non-final user-input sequence is followed by another completed assistant boundary and the final sequence ends in fresh input. Direct intrinsic calls MAY precede an assistant boundary only when terminal completed or failed outputs settle every represented synchronous call in order. An upstream-authenticated asynchronous call MAY remain unresolved across a completed assistant boundary because it is account-neutral, self-contained input rather than an account-owned response anchor; its matching delayed typed output MAY count as fresh follow-up only if retained prior assistant output independently proves completeness. A call at the end of the verified raw prefix MAY be settled by its matching output at the start of the suffix. A direct-call/output sequence alone MUST NOT prove completeness because the persisted metadata does not identify omitted parallel calls. A matching prefix followed only by new user input, empty content, tool-call-only output, in-progress or partial retained output, duplicate, unmatched, or unresolved synchronous calls, or misordered synchronous call output MUST fail closed.

When the upstream-derived pending-tool manifest records an ID as synchronous, a client-supplied `async: true` marker for that ID MUST reject both durable recovery proofs before either can authorize cross-account replay. Marked-asynchronous calls not contradicted by the manifest MUST still pass the ordinary account-neutral item and body validation. Without a durable previous owner or manifest, stateless self-contained fresh replay MAY accept a valid asynchronous marker: no upstream provenance or omitted previous response exists to contradict the marker.

The service MUST validate the complete projected request after removing `previous_response_id`; it MUST reject nonblank conversation or prompt handles, remaining encrypted content, compaction, opaque account-scoped file/container/vector handles, nonportable file schemes, hosted, MCP, program-mediated, or unknown call or tool-choice state, unknown top-level fields, unknown or malformed top-level reasoning configuration, malformed message/content shapes, and tool outputs without exactly one matching intrinsic call. Assistant messages MUST contain only supported output parts, while user, system, and developer messages MUST contain only supported input parts. Inline data images and HTTP(S) file/image content MAY remain eligible. Eligible declared tools, tool choices, and retained direct calls MUST be shape-validated, account-neutral, and self-contained. Web-search filters, context size, and approximate location MUST use only the recognized nested fields and value types. An apply-patch call MUST use exactly one representation: a recognized structured `operation` with its exact discriminated fields, a nonblank legacy `patch`, or a nonblank legacy `input`.

For an eligible replay, the service MUST remove `previous_response_id`, strip every downstream session/turn alias, clear hard affinity, exclude the unavailable owner, prevent initial bridge-owner forwarding, and submit the complete projected request through a fresh server-namespaced recovery lane. It MUST NOT replay after downstream-visible output. Selection policy conflicts, authentication/connection failures after selection, incomplete history, or any unsafe request state MUST remain fail-closed.

#### Scenario: Client-supplied full resend moves from A to B

- **GIVEN** account A owns a completed previous response and its durable row stores the completed input count and fingerprint
- **AND** a follow-up supplies that previous response plus an account-neutral full resend whose retained prefix matches both values
- **WHEN** required-owner selection returns typed `continuity_owner_unavailable` before output
- **THEN** the bridge removes the previous-response anchor and all stale affinity headers
- **AND** excludes account A and submits the complete fresh request once on account B
- **AND** the next turn for the recovered task remains on account B

#### Scenario: Forged asynchronous marker contradicts durable sync manifest

- **GIVEN** account A emits synchronous `call_s` and a completed assistant message, and its durable manifest records `call_s` as synchronous
- **WHEN** its owner is unavailable and a matching full resend omits `call_s`'s output, relabels the call `async: true`, and supplies fresh user input
- **THEN** the bridge MUST return `previous_response_owner_unavailable` without submitting any request on account B
- **AND** the same refusal MUST hold if the only proposed fresh input is a delayed typed output for another async call

#### Scenario: Authenticated asynchronous call spans an owner-loss resend

- **GIVEN** a valid upstream async call does not occur in the synchronous pending manifest and a full resend retains a completed assistant boundary
- **WHEN** the unavailable owner's client supplies fresh user input or the later matching typed async output
- **THEN** the existing account-neutral fresh replay checks MAY recover on another account
- **AND** an async call or typed result without the retained assistant boundary MUST NOT establish completeness alone

#### Scenario: Stateless async input has no durable provenance

- **GIVEN** a self-contained fresh request has no previous response or durable owner
- **WHEN** a validated intrinsic call carries `async: true`
- **THEN** the ordinary account-neutral fresh replay validator MAY admit it without consulting a nonexistent manifest

#### Scenario: Proxy-injected anchor protects an equivalent full resend

- **GIVEN** a hard durable alias resolves a completed response and the incoming full resend matches its retained count and fingerprint
- **AND** the proxy injects that response as the reattach anchor
- **WHEN** required-owner selection returns typed `continuity_owner_unavailable` before output
- **THEN** the same fresh-replay rules apply after the injected anchor is removed

#### Scenario: Verified resend contains owner-bound reasoning

- **GIVEN** a verified full resend contains encrypted reasoning, server-assigned item IDs, and completed web or tool-search bookkeeping
- **AND** its retained assistant and direct-tool content is otherwise complete and portable
- **WHEN** required-owner selection returns typed `continuity_owner_unavailable` before output
- **THEN** the bridge omits the reasoning and search bookkeeping and strips upstream item identities
- **AND** no encrypted content or upstream item identity is sent to account B
- **AND** the validated plaintext projection is submitted once on account B

#### Scenario: Retained request contains account-scoped state

- **GIVEN** a full resend contains a conversation or prompt handle, compaction, encrypted content outside an omitted reasoning item, an opaque account-scoped file/container/vector handle, a nonportable file scheme, hosted or MCP call or tool-choice state, an unknown call type, or an unmatched tool output
- **WHEN** its required owner is unavailable
- **THEN** the request fails with `previous_response_owner_unavailable`
- **AND** none of that state is sent to another account

#### Scenario: Request shape is not completely understood

- **GIVEN** a purported full resend contains an unknown top-level field or malformed/unknown message content
- **WHEN** its required owner is unavailable
- **THEN** replay eligibility fails closed
- **AND** the service does not infer portability from the retained fingerprint alone

#### Scenario: Matching input prefix omits the prior response output

- **GIVEN** the incoming input prefix matches the durable count and fingerprint
- **AND** the suffix contains only a new user message, a direct-call/output sequence without a later completed assistant boundary, partial retained output, or unresolved synchronous calls
- **WHEN** the required owner is unavailable
- **THEN** replay eligibility fails closed with `previous_response_owner_unavailable`
- **AND** the proxy does not drop the previous-response anchor or send the incomplete transcript to another account

#### Scenario: Owner was selected before a later failure

- **GIVEN** the required owner was selected successfully
- **WHEN** refresh, authentication, WebSocket connection, transport, or timeout fails before output
- **THEN** the request keeps that ordinary failure classification
- **AND** the service does not activate cross-account full-resend recovery

#### Scenario: Durable continuity row has no account owner

- **GIVEN** a durable continuity row proves retained input but has no account owner
- **WHEN** the request is evaluated before account selection
- **THEN** the bridge returns `previous_response_owner_unavailable`
- **AND** it does not treat the missing owner as a typed selector miss or replay on another account

#### Scenario: Failure occurs after visible output

- **WHEN** any part of a response has become downstream-visible
- **THEN** the service does not replay the request on another account
- **AND** it terminates through the existing partial-output failure contract
