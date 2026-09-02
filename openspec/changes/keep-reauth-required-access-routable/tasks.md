## 1. Routing and refresh

- [x] 1.1 Apply one active-or-reauth request-routability policy while keeping paused and deactivated accounts blocked.
- [x] 1.2 Skip proactive refresh for `REAUTH_REQUIRED` and fail closed on unchanged terminal material.
- [x] 1.3 Reconcile fresh claimless refresh rows before exchange, safely adopting same-material ciphertext or peer rotation.
- [x] 1.4 Exclude permanent forced-refresh failures from the current request and release leases before failover.

## 2. Continuity and supporting surfaces

- [x] 2.1 Preserve sticky, bridge, and realtime ownership for `REAUTH_REQUIRED`; retain hard-unavailable cleanup.
- [x] 2.2 Clear legacy local unavailable overlays when a routable committed snapshot converges.
- [x] 2.3 Apply canonical eligibility to probes, warmup, automations, usage/reset credits, API-key pools, and dashboard projections.
- [x] 2.4 Quiesce known-expired `REAUTH_REQUIRED` accounts in selection and bridge reuse while preserving hard-owner fail-closed behavior.
- [x] 2.5 Restore explicit all-reauthentication messaging when every candidate has a known-expired access token.
- [x] 2.6 Preserve all-reauthentication messaging ahead of additional-quota evidence rejection.

## 3. Verification

- [x] 3.1 Cover refresh preflight, routing, continuity, and supporting surfaces with regression tests.
- [x] 3.2 Cover expiry gating and all-expired messaging, then run repository lint/type checks and strict OpenSpec validation.
- [x] 3.3 Add the PR author to contributor attribution metadata.
