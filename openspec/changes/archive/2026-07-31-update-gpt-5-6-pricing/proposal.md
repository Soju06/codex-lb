# Update GPT-5.6 usage pricing

## Why

The bundled cost table in `app/core/usage/pricing.py` matches the supplied
current pricing for `gpt-5.6-sol`, but Terra and Luna still use older input,
output, Fast/priority, Flex, and long-context rates. API-key cost limits,
usage reservations, request logs, and reports therefore overstate current
Terra and Luna usage costs.

## What Changes

- Update all supported GPT-5.6 model entries to the supplied standard,
  Fast/priority, Flex, and standard long-context rates.
- Keep the existing 272,000-token boundary and service-tier precedence.
- Keep batch and cache-write pricing out of scope because the proxy does not
  expose corresponding cost-accounting inputs.
- Add regression coverage for both direct pricing calculations and the
  API-key reservation/finalization path.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Cost accounting uses the current GPT-5.6 model and service-tier
  rates.
