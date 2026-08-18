## 1. Report the override on the input-budget fields

- [x] 1.1 Resolve the `/v1/models` input context window from `model_context_window_overrides` when the model has an entry, falling back to the upstream `context_window` otherwise
- [x] 1.2 Clamp an override to the upstream-declared `max_context_window` when upstream provides a positive integer for it, so the reported input budget never exceeds the backend ceiling

## 2. Tests

- [x] 2.1 With an override configured, `/v1/models` reports it on `metadata.input_context_window`, `capabilities.context_length`, `contextLength`, and `context_length` (was the un-overridden upstream window)
- [x] 2.2 An override above the upstream `max_context_window` is reported clamped to that ceiling
- [x] 2.3 Without an override the reported input budget stays the upstream `context_window`, and `/backend-api/codex/models` is unaffected
