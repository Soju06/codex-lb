## Why

The bundled model registry lacks GPT-6 Astra during startup or offline operation. Current main already supplies Astra prices through the shared pricing snapshot and a sufficient fallback client version.

## What Changes

- Add Astra bootstrap metadata from OpenAI Codex `rust-v0.153.4`.
- Normalize supported Cursor-style Astra reasoning and fast suffixes through the existing request-policy path.
- Keep pricing and fallback client-version ownership in the current upstream-metadata implementation.

## Capabilities

### Modified Capabilities

- `model-catalog-compat`: Astra bootstrap metadata and websocket preference.
- `responses-api-compat`: Astra model-label normalization.

## Impact

Registry and request-policy code plus unit and API regressions. No schema, settings, or pricing changes.
