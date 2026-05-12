## Why
Issue #542 reports that `/v1/chat/completions` corrupts parallel tool-call arguments. The Responses API emits `response.function_call_arguments.delta` / `.done` events that identify their owning call only via `item_id` (e.g. `"fc_..."`). The current `ToolCallIndex.index_for` keys on `call_id`/`name` only, so argument events for the second and subsequent parallel calls fall back to index `0` and overwrite the first call's payload. Downstream agents then silently execute the wrong actions.

## What Changes
- Route Responses tool-call events through a `ToolCallIndex` that also recognises `item_id` as an indexing key.
- Establish an alias between the `output_item` `call_id` and the corresponding `item_id` the first time both are seen, so subsequent argument-only events resolve to the same slot.
- Preserve the existing behaviour for Chat-Completions-style events that carry `call_id` directly with no `item_id`.
- Add regression coverage for parallel tool-call routing in both the streaming and non-streaming `/v1/chat/completions` adapters.

## Impact
- Restores correct `tool_calls[].function.arguments` per call when upstream emits multiple parallel function calls.
- No effect on single-tool-call paths, raw `/v1/responses` forwarding, or non-tool text handling.
