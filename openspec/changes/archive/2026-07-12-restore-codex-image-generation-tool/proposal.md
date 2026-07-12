## Why

Codex Desktop can expose the built-in `image_gen` surface through backend
Codex Responses requests that advertise the upstream `image_generation` tool.
codex-lb currently strips that tool on `/backend-api/codex/responses` unless
the request forces `tool_choice`, so ordinary Codex turns lose the descriptor
that enables image-generation flows even though public `/v1/responses` and
`/v1/images/*` already prove upstream accepts the tool.

## What Changes

- Preserve backend Codex `image_generation` tool entries during request
  normalization and upstream forwarding.
- Keep public `/v1/*` Responses behavior unchanged.
- Keep image-generation requests on the HTTP/SSE upstream transport path so
  large image payloads avoid websocket frame limits.
- Add regression coverage for backend Codex normalization with ambient
  `image_generation` descriptors.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: backend Codex Responses routes preserve
  `image_generation` tool descriptors instead of stripping them.

## Impact

- Code: `app/modules/proxy/request_policy.py`.
- Specs: `openspec/specs/responses-api-compat/spec.md` and this change delta.
- Tests: request-normalization regression coverage.
