## Why

OpenAI's upstream Codex catalog now advertises `gpt-6-astra`, but codex-lb's bundled registry, pricing table, aliases, and model-label normalization do not know it yet.

Without a bootstrap entry, startup/offline paths and policy checks can price or route Astra traffic incorrectly before a live account catalog refresh catches up.

## What Changes

- Add `gpt-6-astra` to the static model catalog with the upstream fields from the captured catalog entry.
- Add `gpt-6-astra` pricing for standard, Priority/Fast, Flex/Batch, and long-context requests above 272K input tokens.
- Add pricing aliases for `gpt-6-astra*` and the bare `gpt-6` family alias when the existing bare-family convention applies.
- Add `gpt-6-astra` to Cursor-style model-label normalization so reasoning and fast suffixes map to request fields.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `model-catalog-compat`: add `gpt-6-astra` to the bootstrap catalog and websocket-preferred bootstrap behavior.
- `api-keys`: add `gpt-6-astra` pricing and aliases for cost-based policy accounting.
- `responses-api-compat`: add `gpt-6-astra` to supported GPT-family Cursor-style model aliases.

## Impact

- Affected code: `app/core/openai/model_registry.py`, `app/core/usage/pricing.py`, `app/modules/proxy/request_policy.py`.
- Affected tests: adjacent unit tests for pricing, model registry bootstrap metadata, and request-policy alias normalization.
- No database schema change, live deployment, or user documentation change.
