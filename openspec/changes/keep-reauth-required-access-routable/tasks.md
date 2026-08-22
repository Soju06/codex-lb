## 1. Routing and refresh

- [x] 1.1 Apply one active-or-reauth request-routability policy while keeping paused and deactivated accounts blocked.
- [x] 1.2 Skip proactive refresh for `REAUTH_REQUIRED` and fail closed on unchanged terminal material.
- [x] 1.3 Reconcile fresh claimless refresh rows before exchange, safely adopting same-material ciphertext or peer rotation.
- [x] 1.4 Exclude permanent forced-refresh failures from the current request and release leases before failover.

## 2. Continuity and supporting surfaces

- [x] 2.1 Preserve sticky, bridge, and realtime ownership for `REAUTH_REQUIRED`; retain hard-unavailable cleanup.
- [x] 2.2 Clear legacy local unavailable overlays when a routable committed snapshot converges.
- [x] 2.3 Apply canonical eligibility to probes, warmup, automations, usage/reset credits, API-key pools, and dashboard projections.

## 3. Verification

- [x] 3.1 Cover refresh preflight, routing, continuity, and supporting surfaces with regression tests.
- [x] 3.2 Run changed tests, repository lint/type checks, and strict OpenSpec validation.
