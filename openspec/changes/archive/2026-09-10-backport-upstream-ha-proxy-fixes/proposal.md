## Why

The fork is on beta.5 with deployed key-dashboard groups, while upstream now contains fixes for stale client fingerprints on HA followers, missing trusted subscription hints, code-less burst 429 retries, and rebuilt HTTP headers. Backport these bounded fixes without importing beta.6's transport refactors, settings removals, or migration graph changes.

## What Changes

- Warm the in-process client-version cache on every enabled model-scheduler replica and update the fallback to upstream's verified 0.153.4 baseline.
- Synthesize subscription routing hints from normalized model/tier state across HTTP, WebSocket, bridge reconnects, and compaction; continue discarding inbound hints.
- Strip hop-by-hop and Connection-nominated headers from rebuilt HTTP requests before client-identity classification.
- Apply replica-local burst cooldowns to code-less 429s and bounded same-owner retries while preserving HTTP status, Retry-After, cancellation, and API-key settlement ordering.
- Preserve fork-specific account ownership, stream budgets, UTC+7 limits, key-dashboard, and the deployed migration revision unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `outbound-http-clients`: Replica-local fingerprint warmup and trusted subscription routing hints.
- `responses-api-compat`: Rebuilt HTTP hop-by-hop sanitation.
- `account-routing`: Replica-local burst rejection cooldown without persisted quota state.
- `sticky-session-operations`: Bounded same-owner burst retries and pre-response HTTP wait ownership.

## Impact

Backend client builders, model refresh scheduler, balancer runtime state, streaming retry/probe paths, focused regression suites, and OpenSpec only. No dependency, Rust helper, schema, dashboard UI, environment-variable removal, release-version bump, or production rollout. The existing key-dashboard commit is pushed separately; new implementation commits and deployment require a separate operator request.
