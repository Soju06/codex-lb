## Why

A self-contained inline-image request can use prompt-cache affinity to select
an account that has just reached its upstream usage limit. The streaming
failover path excludes that account after the pre-visible `429`, but does not
mark the soft affinity reallocatable. Selection can therefore retain the
exhausted prompt-cache owner and surface the upstream limit despite another
eligible account being available.

## What Changes

- Reallocate soft prompt-cache or sticky-thread affinity after a pre-visible
  stream rate-limit or quota failover.
- Preserve existing required-owner behavior for previous responses, turn state,
  and uploaded file references.
- Add a regression for an inline-image request that receives a `429` from its
  prompt-cache account and succeeds on another eligible account.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Require pre-visible rate-limit failover to reallocate
  soft affinity before replacement account selection.

## Impact

The change is limited to the direct streaming retry loop, one integration
regression, and the Responses compatibility contract. It adds no setting,
endpoint, schema, migration, dashboard surface, or dependency.
