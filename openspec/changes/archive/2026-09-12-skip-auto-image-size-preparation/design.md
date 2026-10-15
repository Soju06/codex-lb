## Context

See `proposal.md` for motivation. Transport resolution already routes auto image-generation requests to HTTP regardless of the payload size; explicit WebSocket overrides take precedence over that rule.

## Goals / Non-Goals

**Goals:** Complete the existing unconditional-HTTP preparation contract for auto image-generation requests.

**Non-Goals:** Change transport selection, explicit WebSocket preparation, tracing, native request bodies, or fallback behavior.

## Decisions

Cache the existing image-tool predicate and reuse it in transport resolution. Exclude only auto image-generation requests from the current size-estimation condition. Restricting all estimation to auto mode would also change explicit WebSocket preparation, which is outside this fix.

Extend the existing real-origin preparation test with one image-generation case below the normal WebSocket byte budget. This isolates the image-tool routing rule and checks both unused encodes and exact HTTP body identity without duplicating the fixture.

## Risks / Trade-offs

- An oversized fixture could hide a broken image-tool decision. Use the normal byte limit for the image case, while retaining the smaller limit for the existing size-budget case.
- Active consumers still need serialized payloads. Keep the existing enabled-trace, native-client, WebSocket and fallback coverage.
