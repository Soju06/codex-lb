# Interpret Responses WebSocket events in Rust

## Why

HTTP Responses event interpretation already runs in the native helper, but the
native Responses WebSocket path still sends raw JSON text to Python. Python then
parses every frame, applies the same alias classification, and decides terminal
lifecycle. This duplicates the hot path and makes the two transports drift.

## What changes

- Add an opt-in native WebSocket interpretation capability for Responses calls.
- Have Rust ignore invalid/non-object frames as the current Python stream does,
  normalize known event aliases, classify event types, and report terminal-ready
  metadata through the existing multiplexed IPC stream.
- Keep error envelope conversion, request matching, retry/failover, archiving,
  downstream forwarding, and Live WebSocket frames in Python.
- Remove the redundant Python parse/classify fast path when metadata is trusted;
  retain the existing parser for frames explicitly marked for Python normalization
  and for the Python WebSocket implementation.

## Impact

Native Responses WebSocket protocol, Python native adapter, direct/routed
Responses WebSocket integration, fixtures and outbound-client OpenSpec. No new
setting and no change to helper fallback or WebSocket lifecycle policy.
