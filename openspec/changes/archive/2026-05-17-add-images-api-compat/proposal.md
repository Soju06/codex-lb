# Proposal: add-images-api-compat

## Why

OpenAI clients (`openai` SDK, downstream tools, Codex CLI fallback) call `POST /v1/images/generations` and `POST /v1/images/edits` to access GPT Image models such as `gpt-image-2`. Today codex-lb does not expose those endpoints, so any client that wants to use ChatGPT-backed image generation through codex-lb has no entrypoint. Direct calls to `https://api.openai.com/v1/images/...` require a platform organization on the ChatGPT account (token-exchange to `openai-api-key` returns `invalid ID token: missing organization_id` for typical Plus/Pro accounts), which is a separate platform onboarding most ChatGPT users do not have.

The ChatGPT Responses API already exposes the same image generation capability through the built-in `image_generation` tool. Empirical verification on the dev instance (`POST /v1/responses` with `tools: [{"type": "image_generation"}]`) confirms:

- Plain Plus/Pro ChatGPT accounts can invoke `image_generation` and receive a full b64-encoded `gpt-image-2` PNG inside a `image_generation_call` ResponseItem.
- Streaming the same call produces a clean `response.image_generation_call.partial_image` / `response.output_item.done` event sequence carrying `partial_image_b64` and the final `result`.

That makes a thin OpenAI-compatible adapter on top of `/v1/responses` the right fit: the heavy lifting (auth, account routing, sticky session, usage, ChatGPT backend connectivity) already exists, and we only add a translation layer that exposes the OpenAI Images shape.

## What Changes

- Add `POST /v1/images/generations` and `POST /v1/images/edits` to the existing `/v1` router, with OpenAI-compatible request schemas and response shapes (non-streaming JSON and SSE streaming).
- Implement the endpoints as a translation layer that builds an internal Responses request with `tools: [{"type": "image_generation", ...}]`, dispatches it through the existing proxy service (sticky session, account selection, usage, retries), and converts the resulting `image_generation_call` items back into OpenAI Images responses.
- Restrict the public `model` field to the `gpt-image-*` family (default `gpt-image-2`) and mirror codex-cli's per-model parameter rules (e.g. `gpt-image-2` rejects `input_fidelity` and `background=transparent`; size constraints are enforced).
- Map streaming events: `response.image_generation_call.partial_image` → `image_generation.partial_image`, final `image_generation_call` ResponseItem → `image_generation.completed`, errors → OpenAI `error` event.
- Account `image_generation` token usage from `response.tool_usage.image_gen` against codex-lb's existing usage history with a new request kind for image generation.
- Explicitly do NOT expose `/v1/images/variations`. Codex CLI does not call it, and the ChatGPT Responses backend does not provide a tool path that maps cleanly. Requests to that endpoint return 404 (or 400 if added later as an explicit unsupported endpoint).

The internal "host" model used to run the `image_generation` tool stays at the current default (e.g. `gpt-5.5`); it is never echoed to the client. Clients always see only `gpt-image-2` (or whatever they sent in the `gpt-image-*` family).

## Impact

- New OpenAI-compatible image surface on codex-lb without requiring users to onboard to platform.openai.com.
- Reuses the existing proxy/auth/usage stack; no new auth path (no ChatGPT→openai-api-key token exchange) is added.
- Streaming clients see the canonical `image_generation.partial_image` and `image_generation.completed` events instead of the Responses API event names.
- `gpt-image-*` becomes a first-class model family for routing, allowed-model API key checks, and model-scoped usage limits even though it is implemented as a tool over a host model.
- `codex/scripts/image_gen.py` CLI fallback can target codex-lb by setting `OPENAI_BASE_URL` to the codex-lb base and `OPENAI_API_KEY` to a codex-lb-issued key. No upstream codex changes are required.
