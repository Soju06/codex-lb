# Fix undecodable standalone search responses

## Why

Standalone searches now reach upstream successfully, but native egress returns
raw gzip bytes. The Codex control response allowlist omits `Content-Encoding`,
so a successful 200 response reaches Codex Desktop as undecodable JSON.

## What Changes

- Negotiate identity response encoding for opaque unary Codex control requests
  before selecting native or Python transport.
- Replace any inbound `Accept-Encoding` field case-insensitively at its existing
  position; keep the existing payload, authentication, and response contracts.
- Cover compressed client preferences through public search aliases and verify
  successful live search JSON before claiming resolution.

## Impact

- Capability: `responses-api-compat`; Codex control HTTP requests only.
- No new setting, codec dependency, schema, or persistence change.
