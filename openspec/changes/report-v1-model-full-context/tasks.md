## 1. Implementation

- [x] 1.1 Add `/v1/models` metadata fields for full-context semantics: `context_window`, `input_context_window`, and `max_output_tokens`
- [x] 1.2 Normalize known GPT-5 Codex full context windows (`gpt-5.4` = 1M; `gpt-5.5`, `gpt-5.4-mini`, `gpt-5.3-codex` = 400k)
- [x] 1.3 Keep `/backend-api/codex/models` Codex-native `context_window` semantics unchanged

## 2. Tests

- [x] 2.1 Integration: `/v1/models` reports full context windows for `gpt-5.4`, `gpt-5.5`, `gpt-5.4-mini`, and `gpt-5.3-codex`
- [x] 2.2 Integration: `/v1/models` preserves the Codex input budget in `metadata.input_context_window` and exposes `metadata.max_output_tokens`
- [x] 2.3 Integration: `/backend-api/codex/models` still reports the upstream Codex compact-budget `context_window`

## 3. Spec Delta

- [x] 3.1 Add `model-catalog-compat` spec delta covering full-context `/v1/models` metadata and native Codex route preservation
- [x] 3.2 Validate specs locally with `openspec validate report-v1-model-full-context --strict`
