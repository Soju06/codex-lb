# Design

## Wire Contract (from openai/codex)

`codex-rs/core/src/client.rs` builds lite requests as follows:

- `input` starts with `ResponseItem::AdditionalTools { role: "developer", tools }` (serialized as `type: "additional_tools"`), followed by a developer `message` carrying the base instructions; top-level `instructions` is the empty string and top-level `tools` is omitted.
- HTTP transport sets the `x-openai-internal-codex-responses-lite` header per request.
- Websocket transport puts `ws_request_header_x_openai_internal_codex_responses_lite: "true"` into the `response.create` payload's `client_metadata` (the connection is shared across models, so the signal is per request, not per handshake).

codex-lb mirrors this exactly so the upstream backend sees the same request a direct Codex client would send.

## Normalization

`_normalize_responses_input_instructions()` exists for OpenAI-compatible clients that place system/developer messages in `input` (change #950). A lite-shaped request is by construction already in the shape the lite upstream expects, so the lift returns the payload untouched when any input item has `type == "additional_tools"`. This also keeps the developer instructions message in `input` instead of relocating it to `instructions`, preserving byte-level fidelity (prompt caching, upstream lite parsing).

As defense in depth, the lift also skips any individual `system`/`developer` item whose `type` is present and not `"message"`, so unknown future typed items are never folded into instruction text.

## Header Policy

`filter_inbound_headers(..., preserve_responses_lite=True)` keeps the Lite header only when the request's identity headers mark it as a native Codex client (`_is_native_codex_request`). The upstream header builders then re-emit `x-openai-internal-codex-responses-lite: true` when the request-level lite decision is set, on the initial attempt, the HTTP retry attempt, and the compact transport. Non-native requests keep the blanket strip from `strip-internal-responses-lite-header`: upstream rejects the header on non-lite models, and only native Codex clients gate it on model metadata.

The websocket upstream path never sends the header; the client-metadata key is preserved (inbound websocket) or synthesized from the inbound header (HTTP-to-websocket and bridge paths) instead, matching the Codex client's own transport split.

## Failure Modes

- Native lite request, HTTP upstream: without the header the upstream serves a plain text response and the model reports missing tools; with it the grammar `exec` tool is active.
- Native lite request, websocket upstream: the client-metadata key is the only signal; dropping `client_metadata` would silently disable tools, so `apply_codex_installation_metadata` preserves unrelated keys.
- Non-native client sending the header (accidentally or copied config): header still stripped, protecting against upstream `This model is not supported when using X-OpenAI-Internal-Codex-Responses-Lite` rejections.
