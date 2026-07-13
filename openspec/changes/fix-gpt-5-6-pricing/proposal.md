## Why

GPT-5.6 Sol, Terra, and Luna currently fall through the shared pricing registry's broad `gpt-5*` alias because they have no canonical pricing entries or specific aliases. This makes request-log costs and API-key `cost_usd` quota settlement use GPT-5 rates, undercounting the bare `gpt-5.6` alias, Sol, and Terra while overcounting Luna.

## What Changes

- add canonical standard, Flex, Priority, and long-context prices for `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`
- map the bare `gpt-5.6` alias to Sol and add wildcard aliases so suffixed personality model IDs resolve to the matching canonical pricing entry
- add regression coverage for canonical resolution and tier-specific cost calculations
- leave cache-write-token pricing out of scope until the usage model exposes cache-write tokens separately

## Capabilities

### New Capabilities

- none

### Modified Capabilities

- `api-keys`: cost accounting must recognize the bare GPT-5.6 alias, the three personality models, their suffixed aliases, service tiers, and published long-context rates

## Impact

- Code: `app/core/usage/pricing.py`
- Tests: `tests/unit/test_pricing.py`, `tests/unit/test_api_keys_service.py`
- Specs: `openspec/specs/api-keys/spec.md` via this change's delta spec
- No API or database schema changes
