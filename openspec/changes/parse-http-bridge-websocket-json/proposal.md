## Why

The HTTP bridge treats a multiline WebSocket JSON message as one SSE `data:`
line. A valid early upstream error consequently loses its event type, leaves
the request pending, and can become a misleading timeout with an identical retry.

## What Changes

- Decode each WebSocket text message as a complete JSON object.
- Reuse native-interpreted payloads regardless of whitespace.
- Produce valid downstream SSE for multiline objects and preserve existing
  terminal delivery and cleanup. No flag, request normalization, or image changes.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: whitespace-independent HTTP bridge WebSocket events.

## Impact

Bridge parser and narrowly scoped parser/route regressions. Part of #2465
Blocker 1; related reports #2388 and #2493. No dependency on other fixes.
