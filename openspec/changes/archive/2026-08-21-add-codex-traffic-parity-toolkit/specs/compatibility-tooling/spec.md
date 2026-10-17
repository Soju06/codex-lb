## ADDED Requirements

### Requirement: Codex traffic parity tooling preserves transport identity

The compatibility toolkit MUST capture and identify Codex Responses traffic as
HTTP JSON, HTTP SSE, or WebSocket rather than collapsing all three into one
generic stream. It MUST support the public `/v1/responses` path and the native
`/backend-api/codex/responses` and `/codex/responses` path forms. HTTP capture
MUST retain one request/response pair per record; WebSocket capture MUST retain
frame direction and flow identity so multiple `response.create` lifecycles on
one connection can be reconstructed as separate turns.

#### Scenario: HTTP JSON response is classified independently from SSE

- **WHEN** a captured Responses POST returns a JSON content type and body
- **THEN** the record transport is `http_json`
- **AND** it is not reported as an SSE or WebSocket turn

#### Scenario: HTTP SSE response keeps its lifecycle

- **WHEN** a captured Responses POST returns `text/event-stream`
- **THEN** the record transport is `http_sse`
- **AND** the parser retains ordered event types, terminal event, usage, and the
  presence of the `[DONE]` sentinel when supplied

#### Scenario: Long-lived WebSocket carries multiple turns

- **GIVEN** one captured WebSocket flow contains two client
  `response.create` frames
- **WHEN** each create is followed by its upstream lifecycle events and a
  terminal event
- **THEN** the analyzer reconstructs two ordered WebSocket turns
- **AND** it does not merge their events into one turn

### Requirement: Three-path comparison distinguishes baseline from fidelity

The compatibility analyzer MUST accept optional Path A direct traffic plus
required Path B client-to-LB and Path C LB-to-upstream captures. It MUST treat B
and C as the same-run fidelity comparison and MUST report missing turns,
transport changes, request structure, ordered event lifecycle, terminal state,
usage, and tool differences. It MUST treat A as a structural direct baseline
and MUST NOT declare exact generated content, volatile ids, timing, or usage
from a separate A invocation to be a hard proxy mismatch. A strict CLI mode
MUST return a nonzero status when hard B/C mismatches exist.

#### Scenario: Same-run event loss fails strict comparison

- **GIVEN** Path C contains a `response.output_item.done` event in a turn
- **AND** the corresponding Path B turn omits it
- **WHEN** the analyzer runs in strict mode
- **THEN** the report identifies the ordered event lifecycle difference
- **AND** the process returns a nonzero status

#### Scenario: Direct baseline uses a different response id and token count

- **GIVEN** Path A is a separate direct invocation with the same request shape
- **AND** its generated response id and usage differ from Paths B and C
- **WHEN** the analyzer compares all three paths
- **THEN** those direct-run differences remain visible as baseline data
- **AND** they do not by themselves become a hard B/C mismatch

#### Scenario: Transport transition remains visible

- **GIVEN** the client-facing Path B is HTTP SSE
- **AND** the upstream Path C is WebSocket
- **WHEN** their common turn projections otherwise agree
- **THEN** the report identifies the B/C transport transition
- **AND** compares their Responses lifecycle fields without misclassifying the
  WebSocket frames as SSE

#### Scenario: Public Responses adapter rewrites remain comparable

- **GIVEN** Path B uses public `messages` input with instruction, multimodal,
  assistant, or tool-role items
- **AND** Path C contains the corresponding canonical Responses `instructions`
  and `input` items
- **WHEN** metadata capture compares the same-run request legs
- **THEN** known adapter rewrites and synthesized empty defaults do not fail
  strict comparison
- **AND** changed roles, item types, tool names, call identifiers, or continuity
  identifiers remain hard semantic mismatches

### Requirement: Traffic captures fail safe for credentials and body content

The capture addon MUST replace authorization, API-key, cookie, and proxy
credential header values in every capture mode. Metadata-only body capture MUST
be the default and MUST replace sensitive prompt, generated text, tool argument,
tool output, and encrypted-content strings with deterministic digest-and-length
metadata while retaining protocol structure. Raw body capture MUST require an
explicit full mode, and generated capture and report artifacts MUST be excluded
from version control.

#### Scenario: Default capture observes an authenticated request

- **WHEN** metadata mode captures a request with bearer credentials and prompt
  text
- **THEN** the credential value is absent from the record
- **AND** the raw prompt is absent
- **AND** stable digest-and-length metadata remains available for same-run
  equality comparison

#### Scenario: Operator explicitly requests full bodies

- **WHEN** the addon is configured for full body capture
- **THEN** request and response bodies are retained for deep investigation
- **AND** credential headers remain redacted
