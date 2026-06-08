## 1. Implementation

- [x] 1.1 Report `/v1/models` `metadata.context_window` from the backend `context_window` budget by default
- [x] 1.2 Stop promoting raw `max_context_window` or hard-coded full-context guesses into `/v1/models` `metadata.context_window`
- [x] 1.3 Keep `/v1/models` `metadata.input_context_window` explicit and equal to the backend input/context budget
- [x] 1.4 Keep `/backend-api/codex/models` Codex-native catalog semantics unchanged

## 2. Tests

- [x] 2.1 Integration: `/v1/models` reports backend context windows for `gpt-5.4`, `gpt-5.5`, `gpt-5.4-mini`, and `gpt-5.3-codex`
- [x] 2.2 Integration: `/v1/models` does not promote raw `max_context_window` for unknown models
- [x] 2.3 Integration: `/v1/models` preserves the backend input budget in `metadata.input_context_window` and exposes known `metadata.max_output_tokens`
- [x] 2.4 Integration: `/backend-api/codex/models` still reports the backend compact-budget `context_window`

## 3. Spec Delta

- [x] 3.1 Update `model-catalog-compat` spec delta to require backend-context `/v1/models` metadata and native Codex route preservation
- [x] 3.2 Validate specs locally with `openspec validate report-v1-model-full-context --strict`
