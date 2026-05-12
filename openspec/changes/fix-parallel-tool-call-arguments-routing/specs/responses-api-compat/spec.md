## MODIFIED Requirements
### Requirement: Tool call events and output items are preserved
If the upstream model emits tool call deltas or output items, the service MUST forward those events in streaming mode and MUST include tool call items in the final response output for non-streaming mode.

#### Scenario: Tool call emitted
- **WHEN** the upstream emits a tool call delta event
- **THEN** the service forwards the delta event and includes the finalized tool call in the completed response output

#### Scenario: Chat Completions tool arguments avoid snapshot duplication
- **WHEN** `/v1/chat/completions` maps Responses tool-call events that include incremental deltas and later finalized snapshots for the same tool call
- **THEN** the final `tool_calls[].function.arguments` value is exactly one valid JSON string for that tool call
- **AND** the adapter MUST NOT append full snapshot payloads on top of already-collected incremental argument deltas

#### Scenario: Parallel tool calls route arguments by item_id
- **WHEN** `/v1/chat/completions` maps Responses events for two or more parallel function calls whose `response.function_call_arguments.delta` / `.done` events identify the owning call only via `item_id`
- **THEN** each call's arguments are routed to its own `tool_calls[]` slot using `item_id` as the indexing key
- **AND** the adapter MUST alias the `item_id` to the same slot as the `call_id` once both identifiers have been observed, so subsequent argument-only events for that call do not collapse to index `0`
