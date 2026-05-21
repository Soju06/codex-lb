# Change Proposal

The current API key `allowed_models` policy always filters `/backend-api/codex/models` down to the allowlist. That works for standard OpenAI-style model lists, but Codex `/model` also understands per-entry `visibility`, so some operators want to keep the full catalog while hiding disallowed entries instead of removing them outright.

## Changes

- Add an API key boolean option, exposed in the create/edit dialogs as `Apply to codex /model`, defaulting to `false`.
- When the option is `false`, keep the existing `/backend-api/codex/models` allowlist filtering behavior unchanged.
- When the option is `true` and `allowed_models` is non-empty, return the full `/backend-api/codex/models` catalog and rewrite each entry visibility so allowed models become `list` and all remaining models become `hide`.
- When the option is `true` but `allowed_models` is unset or empty, preserve the original `/backend-api/codex/models` behavior because there is no allowlist to apply.
