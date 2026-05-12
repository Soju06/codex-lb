# Accept OpenAI-Style Backend Responses Requests

## Why

Clients that reuse a Codex CLI `base_url` such as `/backend-api/codex` with an OpenAI Responses SDK send `POST /responses` payloads where `instructions` is optional and `input` may be a plain string. The public `/v1/responses` route already accepts this shape, but `/backend-api/codex/responses` still requires the stricter Codex-native request shape, making OpenAI-compatible HTTP clients fail before they can consume the SSE Responses stream.

## What Changes

- Normalize `/backend-api/codex/responses` request bodies through the same OpenAI-compatible Responses request adapter used by `/v1/responses`.
- Keep the route streaming SSE Responses events and preserve existing Codex-native fields such as `instructions`, `session_id` headers, and Codex affinity behavior.
- Add regression coverage with the OpenAI Python SDK against `/backend-api/codex`.

## Impact

- Allows OpenAI-compatible Responses clients to use either `/v1` or `/backend-api/codex` as their base URL.
- Reduces friction for clients that mirror Codex CLI provider configuration.
- Does not change the WebSocket `/responses` contract.
