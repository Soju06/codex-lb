# Migrate Responses stream event interpretation

## Why

Rust frames ordinary Responses SSE bytes but Python still scans each event for
legacy aliases and determines its event type. The synchronous Responses library
can own this work for direct and routed native HTTP, with shared compatibility
fixtures defining the boundary.

## What Changes

- Add a pure Responses event interpreter that preserves unchanged event bytes,
  normalizes supported legacy aliases, and extracts the effective event type.
- Negotiate typed normalized SSE events with bounded text fragments.
- Keep request-context-dependent public error conversion in Python. Explicitly
  hand off alias payloads whose JSON representation cannot be reproduced by the
  Rust serializer (floats, out-of-range integers, escaped surrogate strings).
- Preserve the missing-helper Python path and existing native/SDK modes. Reuse
  the library for WebSocket in a later transport slice.

## Impact

Responses library, egress/protocol, Python adapter and HTTP stream consumption,
shared fixtures, direct/routed integration and architecture documentation.
No routing, retry, settlement, database, or operator setting changes.
