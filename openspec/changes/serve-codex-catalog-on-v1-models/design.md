# Design

## Approach

Branch inside the existing `v1_models` handler in `app/modules/proxy/api.py`: when `request.query_params.get("client_version")` is truthy, delegate to `_build_codex_models_response` (the `/backend-api/codex/models` builder); otherwise keep delegating to `_build_models_response`. The route's `response_model` becomes `None` because the endpoint now serves two documented shapes.

`client_version` is the discriminator because the codex-rs models-manager unconditionally appends it when fetching `<base_url>/models`, while OpenAI-compatible SDKs never send it. No header sniffing or user-agent parsing is needed. An empty value (`?client_version=`) is treated as absent, so accidental empty parameters degrade to the OpenAI-compatible shape rather than surprising a non-Codex caller.

`CodexModelsResponse` already includes both `models` and the OpenAI-compatible `data`/`object` fields, so a mixed consumer that hits the negotiated path still finds the list shape it expects. Codex's catalog deserializer ignores unknown fields, so the extra `data` key is harmless in the other direction.

## Alternatives considered

- Always returning the dual-shape catalog from `/v1/models`: rejected because `ModelListResponse` is `extra="forbid"` today and strict OpenAI-compatible consumers may validate the response shape.
- Fixing only the docs (telling users to use the `/backend-api/codex` provider): rejected; the silent-fallback failure mode is too costly to leave, and `openai_base_url` remains the most discoverable configuration.
