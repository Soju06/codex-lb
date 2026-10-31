## Why

Responses SSE requests can forward an inbound `Accept-Encoding` header to the
native egress helper. The helper then asks `reqwest` to decode a compressed
stream while the stream is still being framed. A truncated or malformed
compressed chunk becomes `error decoding response body`, and the client sees a
disconnect before `response.completed`.

## What Changes

- Normalize `Accept-Encoding` to `identity` on upstream Responses SSE requests
  so the upstream returns an identity SSE body.
- Apply the same header normalization to the native and aiohttp transport paths.
- Preserve compression negotiation for non-streaming requests.
- Add regression coverage for a streamed request carrying an inbound
  `Accept-Encoding` header.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `outbound-http-clients`: Responses SSE requests must use an identity response
  body at the transport boundary.

## Impact

- Affected code: `app/core/clients/codex.py` and Codex client unit tests.
- Affected traffic: streamed Responses HTTP requests only.
- No account, session, database, or API-key data is changed.
