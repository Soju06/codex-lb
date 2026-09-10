## Context

`CodexClient` already owns ordered proxy endpoints and the safety rule that
distinguishes confirmed pre-dispatch connection failure from ambiguous
delivery. The persistent native helper already accepts one concrete proxy URL
per HTTP or WebSocket operation. The correct boundary is therefore a Python
control plane and a native single-attempt data plane.

## Goals / Non-Goals

**Goals:**

- Use the same Codex-family Rust transport for direct and account-routed Codex
  HTTP/SSE/WebSocket traffic.
- Keep route selection, fallback, endpoint metadata, and account-health policy
  unchanged.
- Support the existing request shapes, including query, JSON, raw,
  form-urlencoded, and multipart bodies.
- Fall back to aiohttp only when native dispatch provably did not start.

**Non-Goals:**

- Moving account selection or route resolution into Rust.
- Retrying an ambiguous POST, WebSocket handshake, or WebSocket frame.
- Making source IP, TCP timing, or randomized TLS material identical.

## Decisions

- `CodexClient` discovers or receives the shared native client and prepares an
  immutable wire request before iterating route endpoints.
- Each endpoint is supplied to the helper as one concrete credential-bearing
  proxy URL. Credential-bearing `http://`, `socks5://`, and `socks5h://`
  endpoints are rejected during route resolution, before either connector can
  observe them; credentials require an encrypted `https://` proxy endpoint. The
  helper never chooses a fallback endpoint.
- Native helper unavailability before command dispatch uses the existing
  aiohttp path for that endpoint. Every other native failure is mapped to
  `CodexTransportError` and processed by the existing route safety rules.
- HTTP bodies are serialized once per logical attempt. Multipart boundaries
  and bodies remain identical across safe endpoint fallback attempts.
- A native WebSocket result is wrapped by the native relay adapter while route
  and fallback metadata remain attached by the existing archiver.
- Native errors carry TLS-verification provenance separately from their
  credential-safe message so a non-idempotent request cannot retry a stable
  certificate failure.

## Risks / Trade-offs

- [Multipart encoder drift] -> Cover field, filename, content type, and binary
  payload serialization; fall back before dispatch for unsupported objects.
- [Native startup race] -> Catch only `NativeEgressUnavailable`; never catch a
  dispatched transport/protocol failure as a Python fallback condition.
- [Route metadata loss] -> Continue returning the candidate route selected by
  the Python endpoint loop.
- [Owned aiohttp session leak on native WebSocket success] -> Close only the
  locally-created policy client; the shared native connection remains owned by
  its cached helper client.
