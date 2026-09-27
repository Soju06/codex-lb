## Why

The steering capability currently copies every Astra WebSocket payload, instruments the private aiohttp writer at connection construction, and reads reservations twice during settlement. Maintainer review requires these costs to be confined to the operation that needs them while preserving ownership and accounting.

## What Changes

- Reuse the existing serialized request until steering or completed-parent retention needs configuration.
- Install aiohttp dispatch observation only for a steering-sensitive explicit send. Reject that send safely if the private transport seam is unavailable; ordinary traffic continues unchanged.
- Claim terminal reservation ownership before reading its current items once, preserving concurrent extension and release ordering.

## Capabilities

### Modified Capabilities
- `responses-api-compat`: defer steering-specific payload and transport work.

## Impact

WebSocket dispatch and API-key settlement, without new settings, schema, or wire semantics for supported transports.
