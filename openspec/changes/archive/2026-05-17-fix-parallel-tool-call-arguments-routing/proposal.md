## Why
Issue #542 reports that `/v1/chat/completions` corrupts parallel tool-call arguments. The Responses API emits `response.function_call_arguments.delta` / `.done` events that identify their owning call only via `item_id` (e.g. `"fc_..."`). The previous `ToolCallIndex.index_for` keyed only on `call_id`/`name`, so argument events for the second and subsequent parallel calls fell back to index `0` and overwrote the first call's payload. Downstream agents then silently executed the wrong actions with corrupted parameters.

The fix uses a three-layer routing strategy to be robust against ordering and identifier variations the upstream may emit:

1. **`output_index` routing (primary).** Every Responses API event carries a stable `output_index`, matching the routing key used by OpenAI's own reference client (`openai-python`) and the LiteLLM proxy (BerriAI/litellm#17652). Once `output_index` is observed, all events for that slot resolve through the same `output_index_map` regardless of which identifier they carry.
2. **`call_id` / `name` keying (existing).** Preserved for legacy Chat-Completions-style event streams and any path that already routed by `call_id`.
3. **`item_id` alias (fallback).** When `output_item.added` / `.done` events expose both `item.id` (e.g. `fc_...`) and `item.call_id` (e.g. `call_...`), the `item.id` is registered as an alias to the same slot, so later argument-only events that only carry `item_id` resolve correctly even if their `output_index` was not seen.

A small secondary fix in the same change prevents the upstream `fc_...` item id from leaking into the public `tool_calls[].id` / `call_id` fields exposed to clients: clients continue to see the upstream `call_...` value and never the internal `fc_...` routing handle.

## What Changes
- Add `index_for_output_index(output_index, call_id, name)` on `ToolCallIndex` that prefers a registered `output_index` mapping and falls back to the existing `call_id`/`name` keying. The original `index_for` is preserved unchanged for callers that have no `output_index` (chat completions legacy).
- Add `register_alias(alias_id, index)` and `register_output_index(output_index, index)` helpers, plus a new `output_index_map: dict[int, int]` field, so the indexer can be primed from `output_item.added` / `.done` events.
- Route `response.function_call_arguments.delta` / `.done` events through the new method using their `output_index` as the primary key and the available `item_id` (top-level `item_id` or nested `item.id`) as a routing-id fallback when `call_id` is absent.
- On `output_item.added` / `.done` events that carry both `item.id` and `item.call_id`, register the `item.id` as an alias for the slot, so subsequent argument-only events resolve to the same `tool_calls[]` index.
- Guard `tool_calls[].id` / `call_id` against accepting an `fc_...` item id when the public `call_...` value is what should be surfaced.
- Add regression coverage for the parallel tool-call routing in both the streaming and non-streaming `/v1/chat/completions` adapters.

## Impact
- Restores correct `tool_calls[].function.arguments` per call when upstream emits multiple parallel function calls.
- No effect on single-tool-call paths, raw `/v1/responses` forwarding, or non-tool text handling.
- Public client surface unchanged: `tool_calls[].id` / `call_id` continue to expose the upstream `call_...` value; the `fc_...` item id is only used internally for routing.
- Aligns codex-lb's tool-call routing with OpenAI's reference Responses streaming client and LiteLLM's equivalent fix.
