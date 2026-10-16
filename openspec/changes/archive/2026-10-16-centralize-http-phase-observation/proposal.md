## Why

TTFT can settle on an excluded keepalive, after which verbatim relay bypasses a still-pending upstream phase. Error normalization can also erase the keepalive identity before observation.

## What Changes

- Give the HTTP phase owner responsibility for observation and relay eligibility on every frame.
- Preserve original keepalive provenance through normalization.
- Extend existing route and invariant coverage with the failing sequences and conversion path.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: Require pending phase observation before relay and preserve source eligibility across normalization.

## Impact

HTTP stream processing, the existing SSE carrier, and normalization change. Existing downstream classification, TTFT, byte forwarding, metrics, and persistence contracts remain in force. The independent design and catch map are in `docs/reviews/pr-2112/proposals/round-5.md`.
