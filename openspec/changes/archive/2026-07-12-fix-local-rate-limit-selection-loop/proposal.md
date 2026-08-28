## Why

Single-account pools can hit an upstream 429, persist a local cooldown, and then immediately re-enter the streaming account-capacity wait path because the local balancer error text includes `Try again in Ns`. That local no-account result is not upstream recovery evidence and can create long-lived retry loops instead of failing fast.

## What Changes

- Treat the local balancer `Rate limit exceeded. Try again in ...` message as a terminal no-account selection failure for account-capacity recovery.
- Preserve bounded recovery waits for non-local retry hints such as upstream/workspace capacity guidance.
- Add focused regression coverage for both paths.

## Impact

- Single-account local cooldown exhaustion returns through the normal no-account/rate-limit error path instead of sleeping and retrying the same local failure.
- Multi-account and genuine recoverable upstream capacity waits are unchanged.
