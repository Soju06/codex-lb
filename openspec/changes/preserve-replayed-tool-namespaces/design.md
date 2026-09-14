# Design: Preserve replayed tool namespaces

## Boundary separation

The common `_strip_unsupported_fields` helper keeps its existing opt-in
namespace-removal behavior for callers that target a generic model source. The
ChatGPT-facing `ResponsesRequest` and `ResponsesCompactRequest` serializers
call that helper with removal disabled, as does the replay-safety projection
used by account-neutral continuity checks. This keeps the local `(namespace,
name, call_id)` identity intact through HTTP bridge forwarding, compacted
history, and WebSocket `response.create` frames.

The configured OpenAI-compatible Responses source path calls the explicit
`strip_replayed_tool_call_namespaces_from_payload` helper after constructing a
source payload and before source-specific overrides and tool filtering. The
source path therefore continues to receive the compatibility shape that
motivated the original sanitizer, without changing the ChatGPT wire.

Top-level `namespace` tool declarations are already forwarded byte-preserved;
this change does not alter them. Unknown or malformed input item types are also
left untouched by the explicit sanitizer.

## Data flow

1. Parse the request and retain the original input objects.
2. Build the ChatGPT or compact payload without projecting away recognized
   call namespaces.
3. Run local replay-safety, ownership, and deduplication logic against the
   namespaced projection.
4. Only when a configured generic source is selected, copy and sanitize the
   source payload immediately before dispatch.

This uses the existing helper and a keyword-only boolean at the shared helper
boundary, avoiding duplicate traversal code or a second request model.

## Failure and rollback

The change does not add retries, alter account selection, or weaken replay
safety. If a downstream ChatGPT deployment rejects a namespaced call, reverting
the two serializer call-site flags restores the previous wire projection while
leaving the model-source sanitizer available. The regression tests assert both
the preserved ChatGPT path and the still-stripped source path.
