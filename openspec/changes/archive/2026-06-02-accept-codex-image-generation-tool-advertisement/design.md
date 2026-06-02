## Context

Current Codex Desktop/CLI builds advertise a top-level `image_generation` tool
on backend Codex Responses requests even when the turn is not explicitly asking
for image generation. codex-lb does not need that ambient advertisement to proxy
the turn, and preserving it on the backend Codex path can couple normal Codex
turns to upstream image-tool acceptance. Public `/v1/*` Responses compatibility
already has a separate built-in-tool forwarding contract; this change keeps that
contract unchanged.

The relevant request paths are split across HTTP and websocket entry points, but
both converge on shared request normalization before upstream forwarding.

## Goals / Non-Goals

**Goals:**

- Restore compatibility with current Codex clients on
  `/backend-api/codex/responses`.
- Keep the change narrow to backend Codex routes.
- Preserve the existing built-in tool forwarding policy for public `/v1/*`
  compatibility routes.
- Add regression coverage for both backend Codex HTTP and websocket request
  handling.

**Non-Goals:**

- Changing public `/v1/*` built-in tool validation or forwarding behavior.
- Implementing local image-generation semantics inside codex-lb.
- Changing chat-completions tool validation behavior.

## Decisions

### Sanitize backend Codex payloads before shared validation

Backend Codex HTTP requests and websocket `response.create` payloads will strip
top-level `tools` entries whose `type` is `image_generation` before they pass
through shared `ResponsesRequest` / `V1ResponsesRequest` validation.

Rationale:

- This is the smallest fix that unblocks current Codex clients.
- It avoids changing the public `/v1/*` built-in tool forwarding policy.
- It preserves other tool entries exactly as they are today.

Alternative considered:

- Handle ambient Codex `image_generation` advertisements in shared request
  validation. Rejected because the validator does not know whether a payload
  came from the backend Codex route or a public OpenAI-compatible route.

### Keep sanitization scoped to backend Codex routes

The sanitization hook will run only for `/backend-api/codex/responses` request
handling, including websocket `response.create` payload preparation.

Rationale:

- The compatibility issue is specific to Codex clients.
- Public OpenAI-style routes should keep their existing validation and
  forwarding behavior.

Alternative considered:

- Add special handling inside the shared validator itself. Rejected because the
  validator does not know which external route or client surface triggered the
  request.

## Risks / Trade-offs

- [Upstream begins requiring the advertised tool descriptor for Codex image
  flows] -> Keep the sanitization isolated so it can be revised or removed
  without disturbing `/v1/*` compatibility behavior.
- [Future Codex clients advertise additional ambient built-in tools] -> Cover
  this change with focused regression tests and revisit the backend Codex
  sanitization allowlist if new incompatibilities appear.
- [Sanitization drifts between HTTP and websocket paths] -> Centralize the
  backend Codex tool filtering in shared normalization logic and cover both
  transports in tests.
