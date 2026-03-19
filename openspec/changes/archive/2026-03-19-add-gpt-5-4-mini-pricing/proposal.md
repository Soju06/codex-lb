## Why

OpenAI has published GPT-5.4 mini pricing, but `codex-lb` does not have a canonical entry for that model family yet. Requests that use GPT-5.4 mini snapshots are therefore undercounted in API-key usage and dashboard cost summaries until the shared pricing registry knows about the model.

## What Changes

- add canonical `gpt-5.4-mini` pricing to the shared pricing registry using the published standard, cached-input, and output rates
- add wildcard aliasing for `gpt-5.4-mini` snapshot names so snapshot slugs resolve to the canonical price entry
- add unit coverage for pricing resolution and token-cost calculations for the new model
- update the `api-keys` requirement set so cost-accounting expectations explicitly cover `gpt-5.4-mini`

## Capabilities

### New Capabilities

- none

### Modified Capabilities

- `api-keys`: cost accounting now needs to recognize `gpt-5.4-mini` pricing and snapshot aliases when computing request costs

## Impact

- Code: `app/core/usage/pricing.py`, `tests/unit/test_pricing.py`, `tests/unit/test_dashboard_trends.py`
- Specs: `openspec/specs/api-keys/spec.md` via the change delta
- No API or database schema changes
