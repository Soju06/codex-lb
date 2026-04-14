## Why
Topology-transparent owner handoff is acceptable in steady state, but live rollout tests still show `503 bridge_owner_unreachable` and `previous_response_not_found` during pod restart windows. The current bridge stores live continuity state inside one replica, so restart and readiness races can still break in-flight session continuity.

## What Changes
- Make bridge owner activation rollout-safe so not-ready replicas do not enter active ownership.
- Add durable continuity recovery that survives turn-state alias loss across rollout windows.
- Document the remaining architectural limit between rollout-safe handoff and fully durable upstream session migration.

## Impact
- Reduces rollout-window continuity loss for HTTP `/responses` traffic.
- Clarifies the boundary between rollout-safe continuity recovery and full durable bridge session migration.
