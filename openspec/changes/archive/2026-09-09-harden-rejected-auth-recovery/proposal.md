## Why

PR #2132 review found that rejected-access recovery can weaken deactivation,
miss credential repair committed after rejection, and drop encrypted reasoning
without proving a complete retained turn. The routing specification also has
conflicting refresh-warning eligibility rules.

## What Changes

- Preserve permanent deactivation during rejected-access recovery.
- Atomically reconcile authentication rejection when fresh credentials rotate,
  preserving independent operator and quota state and invalidating routing caches.
- Require retained-turn evidence before removing reasoning for cross-account replay.
- Reconcile relative-availability and refresh-warning routing requirements.

## Capabilities

### Modified Capabilities
- `account-routing`: credential repair ordering and refresh-warning eligibility.
- `responses-api-compat`: fail-closed authentication replay projection.

## Impact

Account token rotation, HTTP Responses retry, replay safety, canonical routing
documentation, and targeted route and concurrency regression tests. No new
configuration or database schema is required.
