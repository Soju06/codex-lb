# Change: Unify routed Codex egress on the native helper

## Why

Direct Codex HTTP/SSE and WebSocket traffic now uses the Codex-family Rust
helper, but an account-bound proxy route still switches the data plane back to
aiohttp. That makes route choice observable through TLS, HTTP, and WebSocket
implementation differences even when request semantics and identity headers
match.

## What Changes

- Keep endpoint ordering, fallback decisions, route metadata, and account
  health ownership in Python.
- Send each selected routed endpoint attempt through the persistent native
  helper for HTTP, SSE, multipart, and WebSocket traffic when the helper is
  available before dispatch.
- Preserve unavailable-only fallback to the existing Python route transport.
- Preserve typed pre-dispatch provenance so only proven-safe attempts can move
  to the next endpoint; never replay an ambiguous request or frame.
- Preserve proxy credentials as data-plane inputs without exposing them in
  helper events, logs, or public errors.

## Impact

- Affected specs: `outbound-http-clients`, `compatibility-tooling`,
  `responses-api-compat`
- Affected code: `app/core/clients/codex.py`, native helper error protocol,
  routed WebSocket wrapping, and their tests
