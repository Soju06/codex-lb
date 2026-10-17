## Why

A transient failure while priming cache-invalidation versions leaves the poller without a baseline, but startup continues and warms routing state. A peer mutation that lands afterward can then be accepted by the first successful background poll as a callback-less baseline, leaving a stale routing decision in service until another bump or restart.

## What Changes

- Make background polling recover fail-safe when no version baseline was recorded: observed positive namespace versions are delivered through their callbacks before they are acknowledged.
- Preserve baseline-only semantics for an explicit `prime()` retry before background polling begins.
- Cover the production-sensitive upstream-route cache and account-routing bridge-reuse paths with two-replica regressions that prove warmed decisions are invalidated after the version read recovers.
- Replace the model-catalog contract's documented callback-less degradation with the same conservative recovery behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `query-caching`: Require the first successful background poll after a failed startup baseline read to reconcile observed namespaces before acknowledging their versions.
- `model-catalog-compat`: Keep surfacing a failed baseline prime while requiring background recovery to invoke the model-registry callback instead of accepting a callback-less first-poll baseline.

## Impact

- `app/core/cache/invalidation.py`: background-poller lifecycle state.
- `app/main.py`: startup failure-semantics documentation.
- Cache-invalidation integration coverage for the warmed account-routing / bridge-reuse path and existing model-registry startup behavior.
- No API, configuration, dependency, database-schema, or migration change.
