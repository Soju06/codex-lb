# Serve the Codex Catalog on /v1/models for Codex Clients

## Summary

Return the Codex model catalog shape (`{"models": [...]}`) from `GET /v1/models` when the request carries a `client_version` query parameter, keeping the OpenAI-compatible list shape for all other callers.

## Motivation

Codex CLI/IDE clients can be pointed at codex-lb in two ways: the `codex-lb`-style provider (`base_url = .../backend-api/codex`) or the simpler `openai_base_url = ".../v1"` override. In the second mode the client fetches its model catalog from `<base_url>/models`, i.e. `GET /v1/models?client_version=<version>` — the codex-rs models-manager always appends that query parameter.

That endpoint currently returns only the OpenAI-compatible shape (`{"object": "list", "data": [...]}`), which the Codex client cannot parse as a catalog. The refresh fails silently and the client falls back to the model metadata bundled in its binary. The failure is invisible until the bundled metadata diverges from what the proxy can serve: with GPT-5.6 (`use_responses_lite = true`, `tool_mode = "code_mode_only"` in the bundled metadata), clients built lite-shaped requests that the proxy could not honor, and every session ran without tools while `/backend-api/codex/models` was serving correct(able) metadata all along.

The proxy already maintains the dual-shape precedent in the opposite direction: `CodexModelsResponse` carries both `models` and `data` so OpenAI-compatible consumers can read `/backend-api/codex/models`. This change closes the remaining gap.

## Scope

- `GET /v1/models` with a non-empty `client_version` query parameter returns the same payload as `GET /backend-api/codex/models` (Codex catalog, which already includes the OpenAI-compatible `data` list).
- `GET /v1/models` without the parameter (or with an empty value) is unchanged.
- API-key model filtering and visibility rules follow whichever builder serves the request, identical to the existing endpoints.

## Out of Scope

- Changing the Codex catalog contents or `CodexModelsResponse` schema.
- The responses-lite request forwarding fixes tracked in #1157.
