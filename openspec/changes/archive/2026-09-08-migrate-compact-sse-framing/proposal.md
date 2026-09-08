# Change: Migrate compact Responses SSE framing to Rust

## Why

Compact requests still scan SSE bytes in Python: routed responses are buffered
before parsing, and direct requests use aiohttp. The existing native framer can
own this work, but compact also accepts JSON success bodies and permits an
unset total timeout. Those contracts must survive the transport cutover.

## What Changes

- Negotiate `http_compact_sse_v1` for content-type-aware SSE options and an
  optional total request timeout. Preserve raw JSON success and HTTP error bodies.
- Consume direct and routed compact Responses unbuffered through native egress,
  retaining the native response until terminal parsing and cleanup finish.
- Keep payload normalization, terminal interpretation, archives, account and
  endpoint selection, replay eligibility, and usage settlement in Python.
- Preserve the missing-helper Python fallback, connection/total/idle budgets,
  cancellation isolation, and no replay after dispatch.

## Impact

- Specs: `outbound-http-clients`, `responses-api-compat`.
- Code: protocol/egress crates, native adapter, CodexClient, compact transport.
- No new dependency, setting, public schema, or deployment action. The adapter
  and bundled helper must be updated together, as with the existing handshake.
