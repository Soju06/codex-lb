## Why

Review found that rate-limit grace can select a capped soft owner and that a first thread binding can commit through a recovery probe while its process seed is missing.

## What Changes

- Check the soft owner's capacity before applying rate-limit grace.
- Defer discretionary recovery probes while a thread needs to initialize its process seed.

## Capabilities

### Modified Capabilities

- `account-routing` covers sticky grace and process-seed initialization.

## Impact

The changes affect sticky account admission and use the existing atomic thread and seed write.
