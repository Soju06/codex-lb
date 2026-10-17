## Why

Binary multiplication while converting per-token source prices to per-million
prices can corrupt a valid decimal rate before exact microdollar settlement.
LiteLLM input price 0.0000002 becomes 0.19999999999999998 and bills 100 tokens
at 19 rather than 20 microdollars.

## What Changes

- Scale validated catalog source prices with decimal arithmetic.
- Retain final fractional-microdollar truncation and existing rejection rules.
- Cover catalog ingestion through settlement and real Responses requests.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `upstream-metadata`: preserve decimal source prices during unit conversion.

## Impact

The existing pricing catalog adapter changes only source-unit scaling. No
dependency, schema, historical-cost rewrite or subscription-capacity change.
