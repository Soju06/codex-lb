## Why

Compact Responses already delegates SSE framing to Rust but sends every event
across IPC for Python to collect output items. Moving that collector beside the
native framer removes per-event Python work and establishes a reusable Responses
library for subsequent application migration.

## What Changes

- Add a pure Rust Responses library owning compact SSE event interpretation,
  indexed/unindexed output collection, and terminal response assembly.
- Negotiate compact collection separately and return a fragmented result through
  IPC as soon as a terminal event arrives, without waiting for HTTP EOF.
- Keep Python public payload normalization, error translation, routing, archives,
  and settlement. Preserve missing-helper fallback and raw JSON/error handling.
- Add shared collector fixtures and direct/routed real-worker regressions.

## Capabilities

### Modified Capabilities

- `outbound-http-clients`: negotiated compact collection and bounded IPC results.
- `responses-api-compat`: native compact collection preserves output and terminal behavior.

## Impact

Cargo workspace, protocol, egress integration, Python adapter and compact client,
contract fixtures, integration tests, and Rust architecture documentation.
