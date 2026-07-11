# Stop Synthesizing Top-Level Tools for Upstream Codex Requests

## Summary

Forward the top-level `tools` field to upstream only when the client actually
sent it, and forward client-sent tool entries byte-preserved. Tool
canonicalization (sorting the array and object keys) becomes a
cache-affinity/observability-only computation and no longer mutates the wire
payload.

## Motivation

Under Responses Lite the Codex client omits top-level `tools` entirely
(codex-rs `build_responses_request` sends `tools = None`) and ships the tool
bundle in the `additional_tools` input item. `ResponsesRequest.tools` is
declared with `default_factory=list`, so `to_payload()` synthesized an
explicit `"tools": []` on every Lite request. gpt-5.6 Sol/Terra
(`multi_agent_version: v2`) configure a reserved `collaboration` namespace
tool (flattened to `collaboration.spawn_agent` in error messages); an explicit
`tools` param triggers reserved-tool schema reconciliation, and `[]` cannot
match, producing `400 invalid_request_error` with `param: "tools"`
(issue #1184).

Secondary hazard: `_canonicalize_tools` (from #228, prompt-cache affinity)
re-sorted every tool and every object key **in the wire payload**, which would
break any byte/structural-equality check upstream performs on a client-sent
reserved tool entry.

## Scope

- `ResponsesRequest.to_payload()` omits `tools` when the field is not in
  `model_fields_set`; an explicit client-sent `[]` is still forwarded.
- `V1ResponsesRequest.to_responses_request()` propagates field omission so the
  OpenAI-compatible route inherits the same behavior.
- Wire payloads forward client tool entries byte-preserved (array order,
  object key order, unknown keys, array-value order). Canonicalization moves
  to `canonicalized_tools()` and feeds only the `_tools_hash`
  affinity/observability consumer, which stays order-insensitive.
- Regression coverage at the product paths: websocket `response.create`
  frame, HTTP-bridge body, and legacy HTTP stream path.

## Sibling-field audit

- `tool_choice` and `parallel_tool_calls` default to `None` and are already
  dropped by `model_dump(exclude_none=True)` when unset; explicit values
  (including Lite's explicit `parallel_tool_calls: false`) keep forwarding.
  No change needed.
- `store: false` is a deliberate proxy invariant (coerced by validator) and
  keeps being emitted.
- `include` shares the `default_factory=list` pattern but a synthesized
  `"include": []` is semantically identical to omission upstream and is not a
  reserved-schema hazard; left unchanged to keep this change one concern wide.

## Out of Scope

- Chat-completions conversion (`ChatCompletionsRequest.to_responses_request`)
  constructs `tools` deliberately; the chat-source path already drops empty
  tool lists (`sanitize_source_chat_payload`).
- Compact requests, which strip `tools` entirely by design.
- Any change to `include`/`store` emission.
