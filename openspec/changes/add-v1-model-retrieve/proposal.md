## Why

Visual Studio Copilot's custom OpenAI-compatible provider validates a configured
model with `GET /v1/models/{model_id}`. codex-lb only serves `GET /v1/models`, so the
probe returns 404 and the provider cannot be added (Soju06/codex-lb#2038).

## What Changes

- Add `GET /v1/models/{model_id}` that returns the OpenAI `model` object for one
  model, identical to the entry `GET /v1/models` emits for that slug.
- Match the path with `{model_id:path}` so slugs containing `/` (for example
  `org/name` from a model source) resolve.
- Reuse the list endpoint's visibility rules (API-key allowlist, source assignment,
  public-model filter) through one shared helper, so a model that is not listed for
  a key is also not retrievable.
- Unknown or not-visible slugs return 404 `model_not_found`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-catalog-compat`: Add single-model retrieval to the OpenAI-compatible
  catalog surface.

## Impact

- Affected code: `app/modules/proxy/api.py`.
- Affected tests: `tests/integration/test_v1_models.py`, plus the route inventory in
  `tests/integration/test_daybreak_capability_routes.py`.
- No schema, persistence, dependency, or configuration changes. `GET /v1/models`
  behavior is unchanged.
