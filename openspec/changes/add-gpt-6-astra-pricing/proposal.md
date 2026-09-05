# Add GPT-6 Astra usage pricing

## Why

`gpt-6-astra` is now served to accounts through upstream model discovery, but it
has no entry in the bundled cost table in `app/core/usage/pricing.py`. The newest
priced family is still `gpt-5.6-{sol,terra,luna}`. `get_pricing_for_model` returns
nothing for an unpriced model, so `calculate_costs` skips it entirely: API-key
cost limits, usage reservations, request logs, and reports all record Astra
traffic as costing nothing, and it silently escapes any configured spend cap.

## What Changes

- Add a `gpt-6-astra` entry with the published standard, Fast/priority, Flex, and
  standard long-context rates.
- Add a `gpt-6-astra*` alias so dated snapshot names resolve to the canonical
  entry, matching the existing family patterns.
- Keep the existing 272,000-token long-context boundary and service-tier
  precedence unchanged.
- Keep batch and cache-write pricing out of scope, as the previous pricing
  changes did, because the proxy exposes no corresponding cost-accounting inputs.
- Add regression coverage for the service tiers, the long-context rates, the
  272,000-token boundary, and snapshot alias resolution.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Require GPT-6 Astra usage cost to be computed from the published
  rates rather than skipped as an unpriced model.

## Impact

- Affected module: `app/core/usage/pricing.py` (one table entry and one alias).
- Affected tests: `tests/unit/test_pricing.py`.
- No API, schema, persistence, dependency, or configuration change.

## Notes

Rates are taken from OpenAI's published API rate card: standard
`10 / 1 / 50` USD per 1M input / cached input / output tokens, Fast at 2x, Flex
and Batch at 0.5x, and prompts over 272,000 input tokens at 2x input and cache
rates with 1.5x output.

Two documented divergences are deliberately **not** encoded, because the existing
table is uniformly the API rate card and encoding surface-specific rates would
change how every other model is read:

- In ChatGPT Work and Codex, Fast for this model is charged at 2.5x standard
  rather than the API's 2x.
- Codex does not apply the long-context multiplier above 272,000 input tokens and
  does not charge for cache writes.

Codex-routed Astra cost will therefore read slightly high for Fast traffic and
for prompts past the long-context boundary. Aligning the table with per-surface
rate cards is a larger contract change and belongs in its own proposal.
