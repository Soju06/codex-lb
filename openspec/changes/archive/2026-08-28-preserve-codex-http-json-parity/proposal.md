# Change: Preserve Codex HTTP JSON parity

## Why

Controlled-origin captures show two remaining server-visible differences
between direct Codex traffic and traffic relayed through codex-lb. A
client-to-LB `x-codex-routing-hint` currently leaks into the upstream request,
and a backend Responses request with `stream: false` is rewritten into an
upstream streaming request. Both transformations make the relayed path
distinguishable even when its TLS, user-agent, and source identity match.

## What Changes

- Treat `x-codex-routing-hint` as hop-local client-to-LB metadata and remove it
  at every upstream HTTP and WebSocket egress boundary.
- Preserve `stream: false` for `POST /backend-api/codex/responses`, force that
  request onto upstream HTTP, consume the single upstream Response JSON, and
  return a single downstream Response JSON.
- Retain the existing `/v1/responses` subscription compatibility path that may
  request an upstream stream and aggregate it when the ChatGPT Codex backend
  requires streaming.

## Impact

- Affected specs: `outbound-http-clients`, `responses-api-compat`
- Affected code: shared inbound-header filtering, Responses HTTP transport
  selection/decoding, backend Responses route dispatch, and parity regression
  tests
