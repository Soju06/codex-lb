## Why

`model_context_window_overrides` is documented as the highest-priority reported-context override, but on `/v1/models` it only reaches `metadata.context_window`. The fields generic OpenAI-compatible clients actually read — `context_length`, `contextLength`, `capabilities.context_length`, and `metadata.input_context_window` — keep reporting the un-overridden upstream `context_window`, so an operator who raises a model's window sees Codex-native clients use the wider window from `/backend-api/codex/models` while every OpenAI-compatible client silently caps itself at the old value. A single catalog then advertises two different budgets for one model.

The split was introduced by `2026-06-02-report-v1-model-full-context` to stop over-advertising context to generic clients, because the backend really did reject inputs above its `context_window` with `context_length_exceeded`. That reasoning still holds for the *default*, but not for an explicit operator override: current frontier models publish a `max_context_window` well above `context_window` (for example `gpt-5.6-sol` at `272000` / `872000`), the backend accepts input up to that ceiling, and the Codex client itself treats a config override as the session window after clamping it to `max_context_window`.

## What Changes

- An explicit `model_context_window_overrides` entry is reported as the input budget too, so `context_length`, `contextLength`, `capabilities.context_length`, and `metadata.input_context_window` agree with `metadata.context_window` instead of contradicting it.
- The reported input budget is clamped to the upstream-declared `max_context_window` when upstream declares one above the backend `context_window`, so an override can never advertise more input than the backend sanctions — the same clamp the Codex client applies to `model_context_window` in `config.toml`. This preserves the original protection against over-advertising while removing the under-advertising. A `max_context_window` equal to `context_window` never clamps: bootstrap subscription models and source-catalog models synthesize that value purely so Codex clients can parse the entry, and treating it as a real ceiling would silently disable raise overrides for those models.
- The override is resolved (and clamped) once per model and that single value feeds `metadata.context_window`, every input-budget field, and the Codex-native `context_window`/`max_context_window` rewrite. An override above the ceiling therefore reports the ceiling everywhere instead of splitting one model into two budgets again (previously `metadata.context_window=1000000` next to `context_length=872000`).
- Behavior is unchanged when no override is configured for the model: the reported input budget stays the upstream `context_window`.
- `/backend-api/codex/models` is affected in two ways, both consistency-preserving: the Codex-native `models` list already reported the override on `context_window` (and rewrote `max_context_window` to match) and now uses the same clamped value, and the endpoint's OpenAI-compatible `data` alias is built from the same list-item shape as `/v1/models`, so its `context_length`-family fields pick up the corrected values too. Both views of one model advertise one budget.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-catalog-compat`: an operator context-window override applies to the OpenAI-compatible input-budget fields, clamped to the upstream `max_context_window` when upstream declares one above the backend `context_window`. The Codex-native catalog requirement now specifies the same single resolved value for the native `context_window`/`max_context_window` rewrite and the endpoint's OpenAI-compatible `data` alias.
