## Why

GPT-6 Astra, Sol, and Luna are absent from the shared pricing table, so their requests have no recognized cost and do not consume API-key cost quotas. Operators need token costs recognized consistently across request logs, reservations, settlement, and usage totals.

## What Changes

- Add verified GPT-6 input, cached-input, output, Flex, Fast/priority, and long-context pricing.
- Resolve each GPT-6 family and its suffixed model names to its own canonical price.
- Apply Fast multipliers to the applicable short- or long-context rates.
- Add regression coverage through the public proxy and request-log APIs as well as pricing calculations.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Recognize GPT-6 request costs with model-specific rates, service tiers, and the 272,000-token boundary.

## Impact

The shared pricing module and cost-accounting tests change. No schema, configuration, frontend layout, or dependency changes are needed. Persisted historical costs and already-settled quotas are not rewritten.
