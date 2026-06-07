## Why

`codex-lb` exposes `/v1/models` as the OpenAI-compatible model catalog for generic SDKs and client frameworks. The endpoint previously promoted Codex backend metadata into a guessed "full context" value (`400000` for `gpt-5.5`/mini/codex and `1000000` for `gpt-5.4`) while carrying the backend budget separately in `metadata.input_context_window`.

That split is unsafe for generic OpenAI-compatible clients because many clients only read `metadata.context_window`. Direct ChatGPT/Codex backend behavior reports `context_window=272000` for these models, and requests above that input budget fail with `context_length_exceeded`. Advertising `400000` or `1000000` therefore makes clients overfill the upstream request and produces retry/fallback failures instead of preflight compression.

## What Changes

- Make `/v1/models` `metadata.context_window` match the upstream backend `context_window` budget by default.
- Stop promoting raw `max_context_window` or hard-coded full-context guesses into `metadata.context_window`.
- Keep `metadata.input_context_window` explicit and equal to the backend input/context budget, so clients that understand the split still have a stable field to read.
- Preserve the existing `model_context_window_overrides` escape hatch as the highest-priority reported-context override.
- Keep `/backend-api/codex/models` behavior unchanged so Codex-native clients continue to receive the backend catalog fields.

## Capabilities

### Added Capabilities

- `model-catalog-compat`: `/v1/models` exposes OpenAI-compatible context metadata that matches the ChatGPT/Codex backend input budget and avoids over-advertising context to generic clients.

## Impact

- **Code**: `app/modules/proxy/api.py`
- **Tests**: `tests/integration/test_v1_models.py`
- **Behavior**: generic OpenAI-compatible clients reading `/v1/models` see the usable backend context window (`272000` for current GPT-5 Codex models) instead of speculative larger windows.
- **Non-goals**: no live probing or dynamic measurement of model limits, no change to account routing, and no request truncation/compaction behavior change.
