## MODIFIED Requirements

### Requirement: Codex compaction triggers are bridged into compact output

When `POST /backend-api/codex/responses` receives a request whose top-level
`input` array contains exactly one `{"type":"compaction_trigger"}` item as its
final element, the proxy SHALL remove that trigger before calling upstream
compaction handling and SHALL emit a raw SSE stream that contains exactly one
compaction output item.

The stream MUST emit `response.created`, `response.output_item.added`,
`response.output_item.done`, and `response.completed` in that order with
monotonically increasing sequence numbers. The added event MUST expose the
selected compaction item as in progress. The done event and terminal completed
response MUST carry the same terminal `compaction` item. When the selected
encrypted upstream compaction item carries a non-empty `id` or `status`, the
synthetic stream MUST preserve those values with its `encrypted_content`; it
MUST NOT generate a replacement item ID.

For Codex-affinity standalone compact requests,
`POST /backend-api/codex/responses/compact` SHALL normalize an upstream
remote-compaction-v2 response that includes historical message output plus a
compaction summary into the single compact output item required by Codex
clients. A non-empty upstream compaction item `id` or `status` MUST be preserved
in that normalized output item.

OpenAI-style `/v1/responses/compact` is unchanged by this requirement.

#### Scenario: terminal trigger emits a complete compact lifecycle

- **WHEN** a `POST /backend-api/codex/responses` request ends with exactly one
  top-level `compaction_trigger`
- **THEN** the proxy strips the trigger and invokes compact handling
- **AND** it emits created, added, done, and completed events in that order
- **AND** their sequence numbers increase monotonically from zero
- **AND** the done event and completed response contain the same single
  terminal compaction item

#### Scenario: encrypted compaction item identity survives trigger streaming

- **WHEN** compaction handling for a terminal trigger returns encrypted content
  with a non-empty upstream `cmp_*` ID and terminal status
- **THEN** the added event exposes that ID with in-progress status
- **AND** the done event and completed response preserve the exact upstream ID,
  terminal status, and encrypted content
- **AND** the proxy does not synthesize a replacement item ID

#### Scenario: malformed trigger placement is rejected

- **WHEN** a `POST /backend-api/codex/responses` request contains a duplicated
  or non-terminal top-level `compaction_trigger` item
- **THEN** the proxy returns HTTP 400 with `invalid_request_error`
- **AND** it does not attempt upstream compaction handling

#### Scenario: Codex-affinity standalone compact normalizes remote v2 output

- **WHEN** a Codex-affinity `POST /backend-api/codex/responses/compact` request
  receives upstream output that contains historical message items and one
  compaction summary item
- **THEN** the JSON response body contains exactly one `output` item for that
  compaction summary
- **AND** the normalized item preserves the compaction summary's non-empty
  upstream ID and status
- **AND** it does not expose historical message items as standalone compact
  output
