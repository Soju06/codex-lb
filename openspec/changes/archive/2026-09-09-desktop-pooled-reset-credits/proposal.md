## Why

Desktop still shows and spends reset credits only from the signed-in account, although LB can route inference across an authorized account pool. Earlier-expiring credits on another account remain hidden.

## What Changes

Add an explicit, default-off dashboard setting for native Desktop reset-credit pooling. Show genuine combined credits and select the earliest-expiring available credit for a default reset. Preserve explicit selections and original-account behavior while pooling is disabled. Pin each request to one owner and credit before upstream consumption so retries cannot spend elsewhere.

## Capabilities

### New Capabilities

- `desktop-pooled-reset-credits`: opted-in native reset inventory and cross-account selection/redemption.

### Modified Capabilities

- `desktop-pooled-usage`: replace only its reset-credit field with the opted-in pooled inventory.

## Impact

Desktop relay/usage integration, reset-credit service and cache, a durable pool-redemption ledger and default-off dashboard column, settings UI, migration and route tests. Depends on #2286. No real credit consumption is authorized for automated verification. Fixes #2288.
