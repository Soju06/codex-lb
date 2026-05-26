## Why

Current Codex app/CLI builds advertise a top-level `image_generation` tool on
`/backend-api/codex/responses` requests even when the turn is not actively
using image generation. The backend Codex route does not need that ambient tool
descriptor to proxy the turn, and forwarding it can make backend Responses
compatibility depend on an upstream tool policy that is unrelated to the user's
request.

## What Changes

- Accept backend Codex Responses requests that include a top-level
  `image_generation` tool advertisement.
- Strip only that advertised top-level `image_generation` tool before shared
  validation and upstream forwarding on `/backend-api/codex/responses` HTTP and
  websocket paths.
- Preserve the existing public `/v1/*` built-in tool forwarding policy and all
  validation behavior for other tool types.
- Add regression coverage for backend Codex HTTP and websocket request shapes
  emitted by current Codex clients.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: backend Codex Responses routes tolerate the client's
  ambient `image_generation` tool advertisement without changing public
  OpenAI-style tool handling.

## Impact

- Code: `app/modules/proxy/request_policy.py`, `app/modules/proxy/service.py`,
  and any shared request-normalization helpers needed to sanitize backend Codex
  tool advertisements before validation.
- Tests: backend Codex HTTP/websocket proxy regression coverage and targeted
  request-normalization unit tests.
- Client compatibility: current Codex app/CLI payloads continue to work against
  codex-lb without changing `/v1/responses` validation or forwarding semantics.
