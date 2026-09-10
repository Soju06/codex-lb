# Native WebSocket routing metadata

## Why

Rust already interprets Responses WebSocket objects, but Python still extracts
payload response IDs and sequence values for every event. Transfer these pure
operations to the reusable Responses crate as the next lifecycle migration slice.

## What Changes

- Include payload response ID and lossless integer sequence metadata in native
  interpreted WebSocket events, negotiated by a new capability.
- Consume the metadata in direct WebSocket matching, archive attribution,
  replay sequence checks, and bridge response matching.
- Preserve Python's validated lifecycle response-ID precedence and downstream
  delivery acknowledgement; pending queues, retry, settlement and socket
  lifetime retain their current owners.

## Impact

Internal helper/adapter contract, Responses crate, Python native adapter and
WebSocket consumers. No new setting, dependency, schema or deployment mechanism.
The Python adapter and bundled helper must be updated together to support
`websocket_responses_routing_v1`; incompatible helpers fail closed before dispatch.
