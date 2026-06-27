# Design

## Approach

Add the exact lowercase `x-openai-internal-codex-responses-lite` header to the shared inbound upstream-header blocklist in `app/core/clients/proxy.py`.

The shared `filter_inbound_headers()` function is already used by proxy service paths before response-create, compact, file, transcribe, warmup, codex-control, HTTP bridge, and streaming egress. The direct upstream builders will also call this shared filter so direct use of the lower-level clients cannot accidentally forward the same header.

The client-facing websocket module already calls the shared filter through `filter_inbound_websocket_headers()`, so adding focused regression coverage there verifies it inherits the same blocklist.

## Header Policy

Only the known unsupported internal Lite header is blocked. The broader `x-openai-client-*` and `x-stainless-*` SDK fingerprint behavior stays unchanged: those headers remain available to existing normalization logic where needed.

## Failure Mode

Before this change, a Codex client could send `X-OpenAI-Internal-Codex-Responses-Lite: 1`; codex-lb would forward it upstream, and upstream could reject the request before model inference. After this change, the request reaches upstream without that internal header.
