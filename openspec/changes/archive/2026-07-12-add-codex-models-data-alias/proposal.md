## Why

Some OpenAI-compatible clients, including JetBrains IDE provider setup, probe the configured base URL by requesting `GET /backend-api/codex/models` and deserializing an OpenAI-style model list with a top-level `data` field. codex-lb's Codex-native endpoint only returned `models`, so those clients rejected the otherwise valid response before they could use the provider.

## What Changes

- Preserve the Codex-native `models` payload for `/backend-api/codex/models`.
- Add an OpenAI-compatible top-level `object: "list"` and `data` model list alias to the same response.
- Keep `data` limited to entries whose Codex visibility is `list`, so hidden Codex catalog entries remain hidden from generic OpenAI-style clients.

## Capabilities

### Modified Capabilities

- `model-catalog-compat`: `/backend-api/codex/models` remains Codex-native while also exposing an OpenAI-style `data` alias for generic client probes.

## Impact

- Code: `app/modules/proxy/api.py`, `app/modules/proxy/schemas.py`
- Tests: `tests/integration/test_v1_models.py`
- Compatibility: JetBrains/OpenAI-style model-list deserializers can read `/backend-api/codex/models` without losing Codex-native clients.
