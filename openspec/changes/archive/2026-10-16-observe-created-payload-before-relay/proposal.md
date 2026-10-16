## Why

The HTTP relay helper classifies canonical frames by SSE header, while phase timing classifies their JSON payload. A token header carrying `response.created` can therefore bypass a still-pending created observation.

## What Changes

- Observe a possible pending created payload before returning the unchanged relay decision.
- Preserve lazy parsing for frames that cannot carry that pending discriminator.
- Extend the route table and owner property with header/payload disagreement and JSON escapes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: define pending created observation when the payload and SSE header differ.

## Impact

The existing HTTP phase owner, timing tests, and owning specification. No wire, storage, retry, or metric-label changes.
