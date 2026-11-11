## Why
A confirmed reset can clear persisted quota status while the same router retains a quota cooldown. Operators currently need a manual pause/reactivate.
## What Changes
Reuse confirmed-reset recovery from PR #2640 and reconcile quota-attributed runtime state using fresh recovery evidence. Preserve manual pause, hard blocks, newer failures and non-quota backoff.
## Impact
Usage refresh and routing; no schema/settings change and no real-credit tests.
