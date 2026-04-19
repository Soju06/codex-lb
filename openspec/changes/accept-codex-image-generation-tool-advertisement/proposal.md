## Why

Current Codex app/CLI builds advertise a top-level `image_generation` tool on
`/backend-api/codex/responses` requests even when the turn is not actively
using image generation. codex-lb currently runs those backend Codex payloads
through the shared Responses tool validator, which rejects `image_generation`
and returns `Invalid request payload` with `param: "tools"` before the request
can reach upstream.

## What Changes

- Accept backend Codex Responses requests that include a top-level
  `image_generation` tool advertisement.
- Strip only that advertised top-level `image_generation` tool before shared
  validation and upstream forwarding on `/backend-api/codex/responses` HTTP and
  websocket paths.
- Preserve the existing unsupported built-in tool policy for public `/v1/*`
  routes and for other unsupported tool types.
- Add regression coverage for backend Codex HTTP and websocket request shapes
  emitted by current Codex clients.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: backend Codex Responses routes tolerate the client's
  advertised `image_generation` tool without broadening public OpenAI-style
  tool acceptance.

## Impact

- Code: `app/modules/proxy/request_policy.py`, `app/modules/proxy/service.py`,
  and any shared request-normalization helpers needed to sanitize backend Codex
  tool advertisements before validation.
- Tests: backend Codex HTTP/websocket proxy regression coverage and targeted
  request-normalization unit tests.
- Client compatibility: current Codex app/CLI payloads continue to work against
  codex-lb without relaxing `/v1/responses` validation semantics.
