# Strip Internal Responses Lite Header

## Summary

Stop forwarding `X-OpenAI-Internal-Codex-Responses-Lite` to upstream Responses and compact endpoints.

## Motivation

OpenAI now rejects some Codex model requests when this internal header reaches upstream with:

`This model is not supported when using X-OpenAI-Internal-Codex-Responses-Lite.`

The header is client/internal control metadata, not part of the public Responses API contract. codex-lb should continue accepting clients that send it, but it must not leak the header to upstream HTTP, compact, or websocket transports.

## Scope

- Strip the Lite header case-insensitively from inbound headers before upstream forwarding.
- Preserve other OpenAI SDK telemetry headers and existing Codex continuity headers.
- Cover HTTP response-create, compact, internal auto-websocket, and client-facing websocket header builders.

## Out of Scope

- Changing model aliases, routing, quota, or auth behavior.
- Reintroducing old `parallel_tool_calls` Lite-body rewriting.
