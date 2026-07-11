## ADDED Requirements

### Requirement: Upstream Responses payloads omit client-omitted request fields

The service MUST NOT emit top-level request fields the client omitted onto
upstream Responses payloads when the field's absence is meaningful upstream.
In particular, the proxy MUST NOT synthesize a top-level `"tools": []` from
the request model's default for clients that did not send the `tools` field,
on any upstream transport (websocket `response.create` frames, HTTP-bridge
bodies, and direct HTTP stream requests). An explicit client-sent
`"tools": []` MUST be forwarded as `[]`. `tool_choice` and
`parallel_tool_calls` MUST be forwarded only when the client sent them;
an explicit client-sent `parallel_tool_calls: false` MUST reach upstream.
The OpenAI-compatible `/v1/responses` conversion MUST propagate `tools`
omission into the native request so both routes behave identically.
Field omission MUST survive every re-serialization hop: the multi-instance
owner-forward body (internal bridge forward) MUST NOT contain fields the
client omitted, the owner instance receiving a forwarded request MUST NOT
re-mark `tools` as explicitly set, and model-source Responses egress payloads
MUST likewise omit fields the client never sent. The owner forward MUST carry
a v2 signature (`x-codex-bridge-signature-v2`) computed over the same
forwarding serialization that is posted as the body, and the forwarding
origin MUST NOT relay externally supplied `x-codex-bridge-*` headers. The
receiving instance MUST treat the v2 signature as authoritative only when it
validates: a valid v2 signature accepts the forward (proving the received
body was not rewritten, including an injected `"tools": []`); an absent or
invalid v2 header falls back to the legacy signature verification; the
forward is rejected only when neither verifies. Mere v2-header presence MUST
NOT block a legacy-signed forward, because pre-v2 origins relay unknown
inbound bridge headers verbatim and an external client could otherwise deny
legitimate forwards by planting a garbage v2 header. For rolling-upgrade
compatibility the origin MUST also keep sending the legacy signature headers
(computed over the plain dump with the synthesized `"tools": []`) so pre-v2
owners verify unchanged. ROLLOUT SHIM: the legacy header emission and the
legacy fallback are a one-release compatibility shim and MUST be removed in a
follow-up change once fleets are homogeneous on a v2-signing release (grep
for `ROLLOUT SHIM` / `HTTP_BRIDGE_SIGNATURE_V2_HEADER`); while the shim is
active the legacy fallback is exactly as strong as the pre-v2 scheme (a
body-only rewrite injecting `"tools": []` into a dual-signed forward
downgrades to the legacy digest and verifies), and removing the shim restores
strict v2-only rejection.

#### Scenario: Responses Lite request reaches upstream without a tools key

- **WHEN** a `/backend-api/codex/responses` request omits top-level `tools`
  and carries its tool bundle in an `additional_tools` input item
- **THEN** the upstream websocket `response.create` frame contains no
  top-level `tools` key
- **AND** the HTTP-bridge request body contains no top-level `tools` key

#### Scenario: Explicit empty tools array is forwarded

- **WHEN** a client sends `"tools": []` explicitly
- **THEN** the upstream payload contains `"tools": []`

#### Scenario: Unset optional tool fields stay absent

- **WHEN** a client omits `tool_choice` and `parallel_tool_calls`
- **THEN** the upstream payload contains neither field

#### Scenario: Owner-forwarded request keeps tools omitted across instances

- **WHEN** a request that omits top-level `tools` is forwarded to its owner
  instance over the internal HTTP bridge (multi-instance owner forward)
- **THEN** the owner-forward request body contains no top-level `tools` key
- **AND** the owner instance parses the forwarded body without marking
  `tools` as explicitly set, so its upstream payload contains no top-level
  `tools` key
- **AND** the owner-forward signature still verifies on the owner instance

#### Scenario: Owner-forward v2 signature covers the posted body

- **WHEN** an owner-forward body that omitted top-level `tools` is rewritten
  in transit to carry an injected explicit `"tools": []`
- **THEN** the v2 signature verification fails
- **AND** absent a valid legacy shim signature, the owner instance rejects
  the forwarded request with an invalid bridge-forward-signature error
  instead of re-marking `tools` as explicitly set
- **AND** generic body rewrites outside the synthesized-tools equivalence
  class fail both digests and are rejected even while the shim headers are
  present

#### Scenario: Mixed-version fleets keep verifying during a rolling upgrade

- **WHEN** an updated origin forwards a dual-signed tools-less body to an
  owner still running pre-v2 code
- **THEN** the legacy signature header matches the pre-v2 owner's
  recomputation over the plain dump, so the forward verifies unchanged
- **WHEN** a pre-v2 origin forwards a legacy-signed body (no v2 header) to
  an updated owner
- **THEN** the updated owner falls back to legacy verification and accepts
  the forward

#### Scenario: Spoofed v2 header does not deny legacy forwards

- **WHEN** a legacy-signed forward from a pre-v2 origin arrives carrying a
  garbage `x-codex-bridge-signature-v2` header that an external client
  planted (pre-v2 origins relay unknown inbound bridge headers verbatim)
- **THEN** the updated owner treats the invalid v2 signature as
  non-authoritative, falls back to legacy verification, and accepts the
  forward
- **AND** an updated origin strips externally supplied `x-codex-bridge-*`
  headers before forwarding, so its own forwards never relay a planted
  header

#### Scenario: Model-source Responses egress omits unsent tools

- **WHEN** a Responses request that omits top-level `tools` is routed to an
  openai-compatible model source
- **THEN** the payload sent to the model source contains no top-level
  `tools` key

### Requirement: Client tool entries are forwarded byte-preserved

The service MUST forward client-sent top-level `tools` entries to upstream
byte-preserved: the tool array order, per-object key order, unknown keys
(including unknown tool types such as `namespace` entries and non-standard
schema markers), and array-value order (for example `parameters.required`)
MUST reach upstream exactly as the client sent them. Tool canonicalization
(array sorting and recursive key sorting) MUST be used only for prompt-cache
affinity and observability hashing and MUST NOT mutate the outgoing payload.
The affinity/observability hash MUST remain insensitive to tool array order
and object key order.

#### Scenario: Reserved namespace tool survives byte-identical

- **WHEN** a client sends top-level `tools` containing a reserved
  `{"type": "namespace", "name": "collaboration", ...}` entry with nested
  function entries, `strict: false`, unknown property markers, and a
  non-alphabetical `required` array
- **THEN** the upstream `response.create` frame serializes that `tools` array
  byte-identical to the client's serialization

#### Scenario: Affinity hash ignores tool ordering

- **WHEN** two requests differ only in tool array order or tool object key
  order
- **THEN** their tools affinity/observability hash is identical
