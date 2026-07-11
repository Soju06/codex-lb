# Derive Responses Lite Signaling from the Request Body

## Summary

Continue blocking untrusted `X-OpenAI-Internal-Codex-Responses-Lite` headers, while preserving real Responses Lite payloads and reconstructing the canonical upstream signal from their `additional_tools` input item.

## Motivation

OpenAI now rejects some Codex model requests when this internal header reaches upstream with:

`This model is not supported when using X-OpenAI-Internal-Codex-Responses-Lite.`

The header is client/internal control metadata, not part of the public Responses API contract. codex-lb should continue accepting clients that send it without blindly forwarding it.

Newer Codex models also use a developer-role `additional_tools` input item as the authoritative Responses Lite body shape. Treating every developer-role item as an instruction silently deletes those tool definitions. Once the body is known to be Lite, the proxy must reconstruct the transport-specific marker instead of trusting the inbound header.

## Scope

- Strip the Lite header case-insensitively from inbound headers before upstream forwarding.
- Preserve `additional_tools` input items during system/developer instruction normalization.
- Derive Lite mode from the normalized request body.
- For upstream HTTP and compact requests, synthesize the canonical Lite header only for a Lite body.
- For upstream websocket requests, synthesize the per-request Lite client-metadata marker while continuing to omit the handshake header.
- Preserve the websocket Lite marker when the HTTP bridge trims stored input prefixes or rebuilds a request for retry.
- Strip an untrusted Lite client-metadata marker unless the same websocket continuity state already established Lite mode from a full body prefix for that model.
- Treat an upstream-accepted full Lite prewarm as establishing continuity so Codex can reuse its response ID with an empty or user-only incremental input delta.
- Preserve other OpenAI SDK telemetry headers and existing Codex continuity headers.
- Cover HTTP response-create, compact, internal auto-websocket, and client-facing websocket request forwarding.

## Out of Scope

- Changing model aliases, routing, quota, or auth behavior.
- Reintroducing old `parallel_tool_calls` Lite-body rewriting.
