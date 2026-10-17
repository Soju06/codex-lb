# Interpret Responses WebSocket events in Rust

## Why

Native Responses WebSocket frames cross IPC as JSON text, then Python decodes and
classifies that text again. The WebSocket relay and HTTP bridge need the original
payload for request matching, sequence tracking and error policy. Transferring
only a type cannot remove that second parse safely.

## What changes

- Opt Responses WebSocket calls into native classification and embed original
  JSON objects in IPC so its decoder provides a reusable policy payload.
- Preserve exact upstream text, aliases, numbers and last-key precedence.
  Invalid/non-object or unsupported frames remain opaque and are forwarded unchanged.
- Reuse native metadata at the actual WebSocket relay and HTTP bridge consumers.
  Remove the unreachable aiohttp-shaped metadata branch from the low-level stream loop.
- Keep error conversion, HTTP normalization, lifecycle and retry/failover policy
  at their existing consumers. Live WebSockets remain opaque.

## Impact

Native Responses WebSocket protocol, Python adapter and policy consumers, shared
wire fixtures and outbound-client OpenSpec. No new setting or transport fallback.
