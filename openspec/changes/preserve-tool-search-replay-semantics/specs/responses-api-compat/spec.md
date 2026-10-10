## ADDED Requirements

### Requirement: Completed tool-search replay stays portable only when client-owned

The Responses replay-safety predicate MUST treat a completed
`tool_search_call` / `tool_search_output` pair as account-neutral only when the
pair is self-contained and any declared execution owner is `client`. The call
MUST carry dictionary `arguments` free of account-scoped or MCP state. The
output MUST carry a `tools` list and MUST NOT carry an `output` field, MUST
NOT be `failed`, and MUST NOT declare `execution: "server"` or any other
non-client owner. Every `tools` element MUST be a loadable tool declaration in
the shape Codex serializes: a `function` or `custom` declaration that passes
the declared-tool account-neutrality rules (with an optional boolean
`defer_loading`), or a `namespace` with a nonblank `name`, optional string
`description`, and a `tools` list of such `function`/`custom` declarations.
Any other element, including MCP, hosted, or container-bound declarations and
nested namespaces, MUST fail closed. The HTTP bridge and WebSocket retry paths
MAY trim a replayed tool-search call after this predicate succeeds, but MUST
preserve its matching output and following user input.

#### Scenario: Client-owned tool-search pair can move accounts

- **GIVEN** replay input containing a completed `tool_search_call` with
  dictionary `arguments`
- **AND** a matching completed `tool_search_output` whose `tools` list holds
  `function` declarations and a `namespace` of deferred `function`
  declarations, as Codex serializes them
- **AND** both items either omit `execution` or declare `execution: "client"`
- **WHEN** the proxy evaluates the replay for account-neutral retry
- **THEN** the tool-search pair is accepted as portable replay state

#### Scenario: Server-owned tool-search output fails closed

- **GIVEN** replay input containing a completed client-owned
  `tool_search_call`
- **AND** its matching completed `tool_search_output` declares
  `execution: "server"`
- **WHEN** the proxy evaluates the replay for account-neutral retry
- **THEN** the replay is rejected as non-portable

#### Scenario: Discovered tool declaration carries account-scoped state

- **GIVEN** replay input containing a completed client-owned tool-search pair
- **AND** its `tool_search_output.tools` contains an MCP declaration, a
  container-bound declaration, an unknown field, or a `namespace` nesting one
  of those
- **WHEN** the proxy evaluates the replay for account-neutral retry
- **THEN** the replay is rejected as non-portable

#### Scenario: Tool-search output uses a shape Codex does not emit

- **GIVEN** replay input whose `tool_search_output` carries a string `output`
  or omits `tools`
- **WHEN** the proxy evaluates the replay for account-neutral retry
- **THEN** the replay is rejected as non-portable

### Requirement: Compact triggers are terminal and singular

The canonical Responses compact endpoint MUST forward one terminal
`compaction_trigger` unchanged. It MUST reject duplicate triggers and
non-terminal trigger states before dispatching upstream work. The OpenAI
compatible `/v1/responses/compact` endpoint MUST retain its existing duplicate
terminal trigger normalization for compatible clients, but MUST reject a
request that contains a `compaction_trigger` whose last top-level input item
is not a trigger before the trailing developer/system hoist can hide it.

#### Scenario: One terminal trigger is forwarded

- **GIVEN** a compact request whose input contains exactly one terminal
  `compaction_trigger`
- **WHEN** the proxy forwards the request upstream
- **THEN** that trigger remains in the forwarded input

#### Scenario: Duplicate trigger is rejected before upstream work

- **GIVEN** a compact request whose input contains more than one
  `compaction_trigger`
- **WHEN** the proxy validates the request
- **THEN** it returns a client error before dispatching upstream work

#### Scenario: OpenAI-compatible compact normalizes duplicate terminal triggers

- **WHEN** a client calls `POST /v1/responses/compact` with duplicate terminal
  top-level `compaction_trigger` items
- **THEN** codex-lb preserves the existing compatibility behavior and returns
  HTTP 200 when the compact operation succeeds
- **AND** the forwarded compact input contains one terminal trigger

#### Scenario: OpenAI-compatible compact rejects a trigger hidden by a trailing developer message

- **WHEN** a client calls `POST /v1/responses/compact` with a
  `compaction_trigger` followed by a trailing developer message
- **THEN** codex-lb returns HTTP 400 with `param: "input"` before dispatching
  upstream work

## MODIFIED Requirements

### Requirement: Verified full resend can recover from selection-time owner loss

An HTTP bridge request MAY move from an unavailable continuity owner to another account only after a typed pre-visible `continuity_owner_unavailable` account-selection result, which the HTTP bridge maps to `previous_response_owner_unavailable`, and positive durable proof that the request contains the complete retained input history. A missing durable owner is not a selector result and MUST fail closed without replay. The durable row MUST provide a positive input-item count and full fingerprint, and the corresponding raw prefix of the incoming list-shaped input MUST match both before any projection occurs.

After the raw prefix proof, the service MUST construct a deterministic plaintext projection by omitting `reasoning` and `web_search_call` items and removing upstream `id` fields from every retained input item. Completed client-owned `tool_search_call` / `tool_search_output` pairs that pass the replay-safety predicate MUST remain in the projection with response-owned IDs stripped, so the replacement account receives the tool-search result instead of rerunning or losing it. Retained `internal_chat_message_metadata_passthrough` MUST contain only a nonblank string `turn_id` when present. The projected suffix after the projected prefix MUST contain a completed assistant `output_text` or `refusal` boundary with nonblank content followed by nonblank fresh text or valid fresh file/image input. The suffix MAY contain multiple intervening turns only when every non-final user-input sequence is followed by another completed assistant boundary and the final sequence ends in fresh input. Direct intrinsic calls MAY precede an assistant boundary only when terminal completed or failed outputs settle every represented call in order. A call at the end of the verified raw prefix MAY be settled by its matching output at the start of the suffix. A direct-call/output sequence alone MUST NOT prove completeness because the persisted metadata does not identify omitted parallel calls. A matching prefix followed only by new user input, empty content, tool-call-only output, in-progress or partial retained output, duplicate, unmatched, or unresolved calls, or misordered call output MUST fail closed.

The service MUST validate the complete projected request after removing `previous_response_id`; it MUST reject nonblank conversation or prompt handles, remaining encrypted content, compaction, opaque account-scoped file/container/vector handles, nonportable file schemes, hosted, MCP, program-mediated, or unknown call or tool-choice state, unknown top-level fields, unknown or malformed top-level reasoning configuration, malformed message/content shapes, and tool outputs without exactly one matching intrinsic call. Assistant messages MUST contain only supported output parts, while user, system, and developer messages MUST contain only supported input parts. Inline data images and HTTP(S) file/image content MAY remain eligible. Eligible declared tools, tool choices, and retained direct calls MUST be shape-validated, account-neutral, and self-contained. Web-search filters, context size, and approximate location MUST use only the recognized nested fields and value types. An apply-patch call MUST use exactly one representation: a recognized structured `operation` with its exact discriminated fields, a nonblank legacy `patch`, or a nonblank legacy `input`.

For an eligible replay, the service MUST remove `previous_response_id`, strip every downstream session/turn alias, clear hard affinity, exclude the unavailable owner, prevent initial bridge-owner forwarding, and submit the complete projected request through a fresh server-namespaced recovery lane. It MUST NOT replay after downstream-visible output. Selection policy conflicts, authentication/connection failures after selection, incomplete history, or any unsafe request state MUST remain fail-closed.

#### Scenario: Client-supplied full resend moves from A to B

- **GIVEN** account A owns a completed previous response and its durable row stores the completed input count and fingerprint
- **AND** a follow-up supplies that previous response plus an account-neutral full resend whose retained prefix matches both values
- **WHEN** required-owner selection returns typed `continuity_owner_unavailable` before output
- **THEN** the bridge removes the previous-response anchor and all stale affinity headers
- **AND** excludes account A and submits the complete fresh request once on account B
- **AND** the next turn for the recovered task remains on account B

#### Scenario: Proxy-injected anchor protects an equivalent full resend

- **GIVEN** a hard durable alias resolves a completed response and the incoming full resend matches its retained count and fingerprint
- **AND** the proxy injects that response as the reattach anchor
- **WHEN** required-owner selection returns typed `continuity_owner_unavailable` before output
- **THEN** the same fresh-replay rules apply after the injected anchor is removed

#### Scenario: Verified resend contains owner-bound reasoning

- **GIVEN** a verified full resend contains encrypted reasoning, server-assigned item IDs, completed web-search bookkeeping, and a completed client-owned tool-search pair
- **AND** its retained assistant and direct-tool content is otherwise complete and portable
- **WHEN** required-owner selection returns typed `continuity_owner_unavailable` before output
- **THEN** the bridge omits the reasoning and web-search bookkeeping and strips upstream item identities
- **AND** no encrypted content or upstream item identity is sent to account B
- **AND** the completed client-owned tool-search pair remains in the projected input without response-owned IDs
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
- **AND** the suffix contains only a new user message, a direct-call/output sequence without a later completed assistant boundary, partial retained output, or unresolved direct calls
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
