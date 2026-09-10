## MODIFIED Requirements

### Requirement: Codex compaction triggers are bridged into compact output

When `POST /backend-api/codex/responses` receives a request whose top-level `input` array contains exactly one `{"type":"compaction_trigger"}` item as its final element, the proxy SHALL remove that trigger before calling upstream compaction handling and SHALL emit a raw SSE stream that contains exactly one compaction output item. The internal compact request built for that flow MUST contain exactly one terminal `compaction_trigger` item on the compact wire, and the proxy MUST reject duplicate or non-terminal top-level `compaction_trigger` placement locally with HTTP 400 `invalid_request_error` before any upstream compact handling.

The stream MUST emit `response.created`, `response.output_item.added`, `response.output_item.done`, and `response.completed` in that order with monotonically increasing sequence numbers. The added event MUST expose the selected compaction item as in progress. The done event and terminal completed response MUST carry the same terminal `compaction` item. When the selected encrypted upstream compaction item carries a valid `cmp_` ID or status, the synthetic stream MUST preserve those values with its `encrypted_content`; it MUST NOT generate or rewrite a replacement item ID. A malformed, empty, or non-`cmp_` ID MUST be omitted while the opaque encrypted content remains unchanged.

Codex compact flows SHALL send the upstream compact request to `POST /backend-api/codex/responses` with `stream=true` and `store=false`, accept the upstream SSE response, and reconstruct one normalized compact response item from the terminal response lifecycle; they MUST NOT require the legacy `/backend-api/codex/responses/compact` upstream route to be available.

For Codex-affinity standalone compact requests, `POST /backend-api/codex/responses/compact` SHALL remain available as a compatibility endpoint with its subscription-backed compact routing contract, and SHALL normalize an upstream remote-compaction-v2 response that includes historical message output plus a compaction summary into the single compact output item required by Codex clients. A valid upstream `cmp_` compaction item `id` and any non-empty `status` MUST be preserved in that normalized output item. An empty, non-string, or non-`cmp_` ID MUST be omitted rather than rewritten; encrypted content MUST remain unchanged.

OpenAI-style `/v1/responses/compact` is otherwise unchanged by this requirement; when it receives duplicate top-level `compaction_trigger` items, codex-lb preserves the existing compatibility behavior and the forwarded compact input contains one terminal trigger.

#### Scenario: terminal trigger emits a complete compact lifecycle

- **WHEN** a `POST /backend-api/codex/responses` request ends with exactly one top-level `compaction_trigger`
- **THEN** the proxy strips the trigger and invokes compact handling
- **AND** it emits created, added, done, and completed events in that order
- **AND** their sequence numbers increase monotonically from zero
- **AND** the done event and completed response contain the same single terminal compaction item

#### Scenario: terminal trigger becomes one compact-wire trigger

- **WHEN** a `POST /backend-api/codex/responses` request ends with exactly one
  top-level `compaction_trigger`
- **THEN** the proxy strips that trigger before compact-input preparation
- **AND** the internal compact request contains exactly one terminal
  `compaction_trigger` item on its `input` array

#### Scenario: encrypted compaction item identity survives trigger streaming

- **WHEN** compaction handling for a terminal trigger returns encrypted content with a non-empty upstream `cmp_*` ID and terminal status
- **THEN** the added event exposes that ID with in-progress status
- **AND** the done event and completed response preserve the exact upstream ID, terminal status, and encrypted content
- **AND** the proxy does not synthesize a replacement item ID

#### Scenario: malformed trigger placement is rejected

- **WHEN** a `POST /backend-api/codex/responses` or
  `POST /backend-api/codex/responses/compact` request contains duplicate or
  non-terminal top-level `compaction_trigger` items
- **THEN** the proxy returns HTTP 400 with `invalid_request_error`
- **AND** it does not attempt upstream compact handling

#### Scenario: Codex compact transport uses the Responses stream

- **WHEN** a valid terminal compaction trigger is submitted through a Codex
  compact flow
- **THEN** the proxy sends the compact request to
  `POST /backend-api/codex/responses` with `stream=true` and `store=false`
- **AND** it accepts the upstream SSE response and reconstructs one normalized
  compact response item from the terminal response lifecycle
- **AND** it does not require the legacy `/backend-api/codex/responses/compact`
  upstream route to be available

#### Scenario: Legacy message-shaped compact output does not get a rewritten item ID

- **WHEN** the upstream compact response exposes the encrypted compact payload
  as a legacy `message` item with a non-empty ID that does not begin with `cmp_`
- **THEN** the proxy converts that item to `type="compaction"` and omits the
  malformed ID
- **AND** the proxy preserves the encrypted content unchanged
- **AND** an existing ID that begins with `cmp_` is preserved byte-for-byte
- **AND** the proxy does not synthesize a `cmp_msg_...` ID
- **AND** ordinary message items outside the compact-output conversion remain
  unchanged

#### Scenario: Standalone Codex compact remains a compatibility endpoint

- **WHEN** a client calls `POST /backend-api/codex/responses/compact`
- **THEN** codex-lb preserves the endpoint and its subscription-backed compact
  routing contract
- **AND** malformed duplicate or non-terminal top-level triggers are rejected
  locally before any upstream compact attempt

#### Scenario: Codex-affinity standalone compact normalizes remote v2 output

- **WHEN** a Codex-affinity `POST /backend-api/codex/responses/compact` request receives upstream output that contains historical message items and one compaction summary item
- **THEN** the JSON response body contains exactly one `output` item for that compaction summary
- **AND** the normalized item preserves the compaction summary's valid `cmp_`-prefixed upstream ID and status
- **AND** it does not expose historical message items as standalone compact output

#### Scenario: OpenAI-compatible compact normalizes duplicate triggers

- **WHEN** a client calls `POST /v1/responses/compact` with duplicate
  top-level `compaction_trigger` items
- **THEN** codex-lb preserves the existing compatibility behavior and returns
  HTTP 200 when the compact operation succeeds
- **AND** the forwarded compact input contains one terminal trigger
