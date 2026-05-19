## Why

`codex-lb` exposes `/v1/models` as the OpenAI-compatible model catalog for generic SDKs and client frameworks. That endpoint currently copies the upstream Codex `context_window` value into `metadata.context_window`.

The upstream Codex catalog's `context_window` is not the same semantic as the context-window value generic OpenAI-compatible clients expect. For the GPT-5 Codex family, upstream reports `context_window=272000` even when the full model window is larger. The value matches the conservative Codex input / auto-compaction budget: for 400k-class models, `400000 - 128000 = 272000`, leaving room for output and hidden reasoning. `gpt-5.4` separately advertises `max_context_window=1000000` while still reporting the same `context_window=272000` compact budget.

Passing the conservative input budget through `/v1/models` makes generic clients believe models such as `gpt-5.5`, `gpt-5.4-mini`, and `gpt-5.3-codex` are 272k-context models rather than 400k-context models, and hides that `gpt-5.4` is a 1M-context model. Native Codex clients still need the conservative compact budget, so changing `/backend-api/codex/models` would be the wrong fix. The OpenAI-compatible `/v1/models` metadata needs its own full-context normalization while preserving the Codex-native fields on the Codex route.

## What Changes

- Normalize `/v1/models` `metadata.context_window` to the full model context for known GPT-5 Codex models:
  - `gpt-5.4` → `1_000_000`
  - `gpt-5.5` → `400_000`
  - `gpt-5.4-mini` → `400_000`
  - `gpt-5.3-codex` → `400_000`
- Add `/v1/models` metadata fields that preserve the split semantics:
  - `input_context_window` carries the Codex-native conservative input / compact budget (`272000` for these models).
  - `max_output_tokens` carries the output/reasoning reserve (`128000` for these GPT-5 Codex models).
- Keep `/backend-api/codex/models` behavior unchanged so Codex-native clients continue to receive the upstream compact-budget semantics.
- Preserve the existing `model_context_window_overrides` escape hatch as the highest-priority reported-context override.

## Capabilities

### Added Capabilities

- `model-catalog-compat`: `/v1/models` exposes OpenAI-compatible full-context metadata without conflating it with Codex-native compact-budget metadata.

## Impact

- **Code**: `app/modules/proxy/schemas.py`, `app/modules/proxy/api.py`
- **Tests**: `tests/integration/test_v1_models.py`
- **Behavior**: generic OpenAI-compatible clients reading `/v1/models` see the intended full context windows; native Codex endpoint semantics remain unchanged.
- **Non-goals**: no live probing or dynamic measurement of model limits, no change to account routing, and no change to request truncation/compaction behavior.
