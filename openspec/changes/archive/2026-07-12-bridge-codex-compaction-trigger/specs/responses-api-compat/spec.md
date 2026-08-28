## ADDED Requirements

### Requirement: Codex compaction triggers are bridged into compact output

When `POST /backend-api/codex/responses` receives a request whose top-level `input` array contains exactly one `{"type":"compaction_trigger"}` item as its final element, the proxy SHALL remove that trigger before calling upstream compaction handling and SHALL emit a raw SSE stream that contains exactly one compaction output item.

The stream MUST include a `response.output_item.done` event whose `item` is a `compaction` record, and the terminal `response.completed` event MUST carry the same single compaction item in `response.output`.

For Codex-affinity standalone compact requests, `POST /backend-api/codex/responses/compact` SHALL normalize an upstream remote-compaction-v2 response that includes historical message output plus a compaction summary into the single compact output item required by Codex clients.

OpenAI-style `/v1/responses/compact` is unchanged by this requirement.

#### Scenario: terminal trigger is converted into a compact stream
- **WHEN** a `POST /backend-api/codex/responses` request ends with exactly one top-level `compaction_trigger`
- **THEN** the proxy strips the trigger, invokes compact handling, and streams one `response.output_item.done` event containing a `compaction` item
- **AND** the terminal `response.completed` event carries that same item in `response.output`

#### Scenario: malformed trigger placement is rejected
- **WHEN** a `POST /backend-api/codex/responses` request contains a duplicated or non-terminal top-level `compaction_trigger` item
- **THEN** the proxy returns HTTP 400 with `invalid_request_error`
- **AND** it does not attempt upstream compaction handling

#### Scenario: Codex-affinity standalone compact normalizes remote v2 output
- **WHEN** a Codex-affinity `POST /backend-api/codex/responses/compact` request receives upstream output that contains historical message items and one compaction summary item
- **THEN** the JSON response body contains exactly one `output` item for that compaction summary
- **AND** it does not expose historical message items as standalone compact output
