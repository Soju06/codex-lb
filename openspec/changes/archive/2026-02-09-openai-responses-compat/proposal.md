## Why

OpenAI Responses API adoption is accelerating, while a large base still relies on Chat Completions; codex-lb needs full wire compatibility to remain a drop-in backend for both. Doing this now reduces client-side branching and migration risk, while explicitly honoring ChatGPT backend limits.

## What Changes

- Implement OpenAI-compatible `/v1/responses` request/response validation, payload shapes, error envelopes, and SSE streaming semantics.
- Expand `/v1/chat/completions` compatibility to match OpenAI response behavior (tool calls, response_format, streaming chunks, usage).
- Provide deterministic mapping between Responses and Chat where required, with strict validation and OpenAI-style errors (no silent fallbacks).
- Add compatibility test coverage using the official OpenAI client and the Codex client in `./refs/codex` to validate wire-level parity.
- Document and enforce backend limitations with explicit, typed errors instead of partial or ambiguous behavior.

## Capabilities

### New Capabilities
- `responses-api-compat`: Full OpenAI Responses API behavior for request validation, response payloads, SSE event stream shapes, and error envelopes (within backend limits).
- `chat-completions-compat`: Full OpenAI Chat Completions API behavior and streaming semantics, including tool_calls and response_format mapping.

### Modified Capabilities

## Impact

- API surface: `/v1/responses`, `/v1/chat/completions`, and related streaming behavior.
- Core mapping/parsing: `app/core/openai/*`, `app/core/clients/proxy.py`, and proxy service/route layers.
- Logging/usage: request log fields, usage token mapping, and rate-limit headers.
- Tests: new integration and unit tests for wire-compatibility with official OpenAI and Codex clients.
