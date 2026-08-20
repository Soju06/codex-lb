## 1. Report the override on the input-budget fields

- [x] 1.1 Resolve the `/v1/models` input context window from `model_context_window_overrides` when the model has an entry, falling back to the upstream `context_window` otherwise
- [x] 1.2 Clamp an override to the upstream-declared `max_context_window` when upstream declares one above the backend `context_window`, so the reported input budget never exceeds the backend ceiling; never treat the synthesized `max_context_window == context_window` parseability default (bootstrap and source-catalog models) as a ceiling
- [x] 1.3 Resolve the override once per model into a single clamped value shared by `metadata.context_window`, the input-budget fields, and the Codex-native `context_window`/`max_context_window` rewrite, so no field pair can disagree

## 2. Tests

- [x] 2.1 With an override configured, `/v1/models` reports it on `metadata.input_context_window`, `capabilities.context_length`, `contextLength`, and `context_length` (was the un-overridden upstream window)
- [x] 2.2 An override above the upstream `max_context_window` is reported clamped to that ceiling on every field, including `metadata.context_window` and the Codex-native `context_window`/`max_context_window`
- [x] 2.3 Without an override the reported input budget stays the upstream `context_window`
- [x] 2.4 The `/backend-api/codex/models` OpenAI-compatible `data` alias reports the override on its `context_length`-family fields (pin: it shares the `/v1/models` list-item shape)
- [x] 2.5 Route-level source-model regression: a raise override on a source-catalog model (synthesized `max_context_window == context_window`) applies unclamped on `/v1/models`
