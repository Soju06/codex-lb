## Why
Reset evidence discovery currently compares microsecond timestamps to a whole-second block marker, while guarded recovery converts the selected baseline back to whole seconds. An early sample in the block second can be selected forever and then rejected, hiding a later valid baseline after an early quota reset.

## What Changes
- Align persisted-history filtering with the existing whole-second recovery guard.
- Cover fractional same-second samples, missing eligible baselines, and the real scheduler/database path.
- Keep quota anchoring, cooldown, exhausted-window checks, compare-and-set protection, and cache invalidation unchanged.

## Impact
- Affected capability: usage-refresh-policy.
- No settings, schema, credential, API, provider, routing, or deployment changes.
