## Context

The proxy currently chooses upstream Responses transport in `app/core/clients/proxy.py`. In `auto` mode it prefers websocket for native Codex headers and websocket-preferred models such as `gpt-5.4`. That works well for normal text flows, but `image_generation` is different: OpenAI documents that the tool returns generated image data inline as base64, so large events are normal rather than pathological.

The recent 16 MiB limit increase reduces failures, but it still applies one shared ceiling to both SSE event buffering and websocket message frames. Continuing to route image-generation traffic over websocket keeps the proxy exposed to avoidable large-frame failures and forces operators to keep raising a global limit for a narrow tool-specific behavior.

## Goals / Non-Goals

**Goals:**

- Make auto upstream transport choose the safer HTTP/SSE path for Responses requests that include `image_generation`.
- Preserve existing operator controls and existing websocket preference logic for non-image-generation requests.
- Keep the change local to transport selection without altering request payload semantics.

**Non-Goals:**

- Do not mutate image-generation tool options such as `size`, `quality`, `format`, `compression`, or `partial_images`.
- Do not retry an already-started websocket image-generation request over HTTP.
- Do not change compact routing, which already strips tool fields before calling the upstream compact endpoint.

## Decisions

### Detect `image_generation` from the serialized Responses payload

Use the already-materialized payload dictionary in `stream_responses()` to determine whether the request contains `tools[*].type == "image_generation"`.

Why:

- The payload is already canonicalized there, so the check is cheap and uses the same data that will go upstream.
- This avoids duplicating request-model-specific logic elsewhere in the proxy stack.

Alternative considered:

- Recompute the signal from the Pydantic request model in multiple call sites. Rejected because it spreads the policy across more than one layer.

### Override only `auto` transport

When `upstream_stream_transport` resolves to `auto`, `image_generation` forces upstream HTTP. Explicit `http` or `websocket` settings still take precedence.

Why:

- Operator overrides should stay authoritative.
- The issue is specifically that the default heuristic chooses websocket for requests whose payload shape makes HTTP safer.

Alternative considered:

- Force HTTP even when the operator explicitly configured websocket. Rejected because it breaks the existing transport-control contract.

## Risks / Trade-offs

- [Image-generation requests may lose websocket-specific latency benefits] → This is intentional; correctness and transport fit matter more than websocket preference for large image payloads.
- [Future built-in tools may have similar large-payload behavior] → Keep the rule narrow for now and extend only when real evidence justifies it.
- [Large HTTP/SSE events still rely on the configured byte ceiling] → The existing 16 MiB default remains in place as a separate safeguard.

## Migration Plan

- Deploy the transport-selection change without config migration.
- If operators explicitly want websocket for image generation despite the risk, they can still force `upstream_stream_transport=websocket`.
- Rollback is a code rollback only; there is no persisted state change.

## Open Questions

- None.
