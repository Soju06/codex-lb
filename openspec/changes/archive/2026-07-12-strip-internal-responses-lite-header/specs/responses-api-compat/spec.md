## ADDED Requirements

### Requirement: Responses Lite signaling is derived from the normalized body

The service MUST accept Responses and compact requests that include
`X-OpenAI-Internal-Codex-Responses-Lite`, but MUST remove that inbound header
case-insensitively before generic upstream-header forwarding. The service MUST
NOT strip unrelated OpenAI SDK telemetry headers solely because they start with
`x-openai-`.

When an input array contains an item with `type = "additional_tools"`,
instruction normalization MUST leave the entire input array and top-level
`instructions` field unchanged. In particular, neither the tool item nor an
adjacent developer instructions message may be extracted from the native Lite
input prefix. The presence of the `additional_tools` item in the normalized
input array MUST be the authoritative signal that the request uses Responses
Lite.

If compact-request size handling trims oversized conversation history, it MUST
retain the `additional_tools` item and its immediately following developer
instructions message. The resulting compact payload MUST therefore retain the
body signal needed to synthesize the canonical Lite header.

For a Responses Lite body, upstream HTTP Responses and compact requests MUST
include the canonical `x-openai-internal-codex-responses-lite: true` header.
Upstream websocket handshakes MUST omit that header and each websocket
`response.create` body MUST instead include
`client_metadata.ws_request_header_x_openai_internal_codex_responses_lite = "true"`.
For a non-Lite HTTP body, the proxy MUST omit the synthesized HTTP header. A
websocket marker on an incremental frame without the full Lite input prefix MAY
remain only when the same request continuity state previously received
`response.created` for a Lite request derived from `additional_tools` using the
same effective upstream model, and the frame's `previous_response_id` references
the response ID recorded by the most recent such Lite acceptance. A frame
without a `previous_response_id`, or one referencing any other response, MUST
NOT receive trusted Lite treatment. The recorded acceptance ID MUST be the
response ID exposed downstream: when a transparent replay suppresses its
`response.created` and keeps rewriting events to the originally visible
response ID, Lite continuity records that visible ID rather than the hidden
upstream replay ID. The effective model comparison MUST occur
after alias normalization and API-key enforcement, and a merely prepared request
MUST NOT establish or clear trusted Lite continuity. Trusted state MUST update
in upstream request-acceptance order rather than terminal-event completion
order, and acceptance of a non-Lite request MUST NOT clear previously recorded
Lite continuity.
An accepted `generate = false` prewarm derived from an `additional_tools` prefix
MUST establish the same trusted continuity because a later request MAY reuse its
response ID without repeating that prefix.
A transparent fresh full-resend replay that clears `previous_response_id` (for
example after an upstream previous-response miss) severs that linkage, so the
replayed request MUST NOT carry the reserved marker unless its own input
contains the `additional_tools` prefix. Acceptance of such a replay MUST
reflect the replayed body: a marker-stripped replay MUST NOT be recorded as a
Lite acceptance (later frames referencing the replay's response ID are not
trusted), while a replay whose input retains the `additional_tools` prefix
MUST re-establish trusted Lite continuity.
Otherwise, the proxy MUST strip the reserved client-metadata marker. The
HTTP-to-websocket bridge MUST preserve its internally derived canonical marker
when it trims an already-stored input prefix or rebuilds the request during
forwarding or retry, even if the remaining input delta has no `additional_tools`
item.

#### Scenario: Instruction normalization preserves Lite tools and tool history

- **WHEN** a request input contains an `additional_tools` item, developer text,
  custom tool calls, and `custom_tool_call_output` items
- **THEN** top-level `instructions` remains unchanged
- **AND** the developer text, `additional_tools`, custom calls, and outputs all
  remain in their original input order

#### Scenario: HTTP and compact synthesize Lite only from the body

- **WHEN** a normalized HTTP Responses or compact payload contains an
  `additional_tools` input item
- **THEN** the upstream request includes
  `x-openai-internal-codex-responses-lite: true`
- **AND** the original inbound Lite header value is not forwarded verbatim

#### Scenario: Compact trimming retains the Lite prefix

- **GIVEN** an oversized Responses Lite compact input whose tool bundle exceeds
  the normally retained head budget
- **WHEN** compact size handling trims conversation history
- **THEN** the `additional_tools` item and adjacent developer instructions stay
  in their original order
- **AND** the upstream compact request includes the canonical Lite header

#### Scenario: Websocket uses a per-request Lite marker

- **WHEN** a websocket `response.create` payload contains an `additional_tools`
  input item
- **THEN** the upstream websocket handshake omits the Lite header
- **AND** the forwarded `response.create` payload contains the canonical
  per-request Lite client-metadata marker

#### Scenario: HTTP bridge trimming preserves Lite metadata

- **GIVEN** an HTTP Responses Lite request whose stored input prefix contains
  the `additional_tools` item
- **WHEN** the HTTP-to-websocket bridge trims that prefix and forwards only the
  new input delta
- **THEN** the forwarded `response.create` payload still contains the canonical
  per-request Lite client-metadata marker

#### Scenario: Incremental websocket marker requires trusted Lite continuity

- **GIVEN** a websocket request received `response.created` after establishing
  Lite mode from an `additional_tools` prefix for its effective upstream model
- **WHEN** a later same-model incremental frame contains the canonical marker,
  omits the already-known prefix, and its `previous_response_id` references the
  accepted Lite response
- **THEN** the forwarded frame retains the canonical marker
- **BUT WHEN** a request for another model supplies that marker without a Lite
  prefix or trusted same-model continuity
- **THEN** the proxy strips the marker
- **BUT WHEN** a same-model frame supplies that marker without a
  `previous_response_id`, or with one referencing a response other than the
  accepted Lite response
- **THEN** the proxy strips the marker
- **AND** the recorded Lite continuity remains available to later frames that
  do reference the accepted Lite response

#### Scenario: Suppressed-created replay keeps Lite continuity on the visible id

- **GIVEN** a Lite websocket request whose `response.created` was already sent
  downstream when the upstream connection is lost
- **WHEN** the proxy transparently replays the request, suppresses the new
  `response.created`, and rewrites downstream events to the original visible
  response id
- **THEN** a later same-model marker-only frame whose `previous_response_id`
  references the visible response id keeps the trusted marker
- **BUT WHEN** a frame references the hidden upstream replay id instead
- **THEN** the proxy strips the marker

#### Scenario: Fresh replay of a trusted incremental frame drops the marker

- **GIVEN** a trusted marker-only incremental websocket frame whose
  self-contained multi-item input yields a transparent fresh full-resend replay
- **WHEN** upstream reports the referenced previous response as not found and
  the proxy replays the request without `previous_response_id`
- **THEN** the replayed request omits the reserved client-metadata marker
- **AND** the accepted replay is not recorded as a Lite acceptance, so a later
  same-model frame carrying the marker with `previous_response_id` referencing
  the replay's response is not trusted and has its marker stripped
- **BUT WHEN** the replayed input itself contains the `additional_tools` prefix
- **THEN** the replayed request retains the canonical marker
- **AND** the accepted replay re-establishes trusted Lite continuity for later
  frames referencing its response ID

#### Scenario: Accepted Lite prewarm authorizes incremental reuse

- **GIVEN** a same-model Lite prewarm containing `additional_tools` receives
  `response.created`
- **WHEN** Codex reuses that response ID in a later frame with the canonical
  marker but without the already-sent Lite prefix
- **THEN** the forwarded frame retains the canonical marker whether its input
  delta is empty or contains new user input

#### Scenario: Stale inbound headers do not enable a non-Lite request

- **WHEN** an HTTP request has no `additional_tools` input item but includes an
  inbound Lite header
- **THEN** the upstream HTTP request omits the Lite signal
- **AND** existing Codex continuity and unrelated OpenAI telemetry headers are
  preserved
