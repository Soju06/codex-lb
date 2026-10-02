## Why

On 2026-10-02, production warm HTTP Responses bridge turns repeatedly reported account stream exhaustion for about fifteen minutes while fresh connections to the same account continued succeeding. Initial selection used the dashboard stream cap of 128, but idle-session reacquisition omitted the cap snapshot and fell back to the startup default of 8; the deployed source matches the affected code on main.

## What Changes

- Resolve effective account concurrency caps, fair-share threshold, and routing tunables from one dashboard snapshot before taking the session pending lock for lease reacquisition.
- Pass that snapshot explicitly for keyed and unkeyed warm sessions; keep already-leased submissions settings-free.
- Preserve real cap refusals, partitioning, unlimited/inherited limits, fair-share accounting, reservation settlement, and cancellation-safe lease release.
- Add public Responses-route regressions with distinct startup/dashboard limits and real load-balancer leases, plus partial-failure and settings-change coverage.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: require idle HTTP bridge stream reacquisition to honor the same effective configured cap contract as initial selection, without settings I/O under the pending lock.

## Impact

HTTP bridge request submission and its tests; admission requirements/context. No new settings, schema, dependencies, UI, or changes to quota policy. Production investigation is read-only; rollout requires an explicitly approved plan, not an incidental restart or limit increase.
