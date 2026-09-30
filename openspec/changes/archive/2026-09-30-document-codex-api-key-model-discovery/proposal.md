## Why

Codex 0.159.0 treats a provider configured with `env_key` as API-key
authentication for model discovery, even when `requires_openai_auth` is true.
Without the discovery feature and an explicit catalog URL for a custom
provider, it uses its bundled model list instead of fetching codex-lb's
catalog. Newly available models can therefore be absent from the app's picker
even when codex-lb advertises them correctly.

## What Changes

- Enable API-key model discovery in the published machine-local Codex example.
- Give both documented Codex providers explicit native model catalog URLs.
- Explain the client-side discovery requirements and their distinction from
  selecting a model by name.
- Record the configuration contract and verification evidence in OpenSpec.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-catalog-compat`: require published Codex provider examples to enable
  discovery of the native catalog when an API key is configured.

## Impact

Documentation, the downloadable TOML example, and its existing local test
harness only. No proxy routing,
authentication, response schema, model defaults, or deployment changes.
Existing capability headers remain confined to the opt-in provider.
