## 1. Routing and refresh semantics

- [x] 1.1 Include `REAUTH_REQUIRED` in request selection while keeping paused and deactivated accounts hard-blocked; verify load-balancer and sticky-selection unit tests.
- [x] 1.2 Suppress proactive refresh for `REAUTH_REQUIRED` and fail closed on forced refresh with unchanged terminal material; verify auth-manager unit tests.
- [x] 1.3 Reconcile fresh claimless refresh rows before exchange, adopting same-material ciphertext or genuine peer rotation safely; verify CAS regression tests.
- [x] 1.4 Exclude permanently failed refresh accounts from the current request retry pool and release leases before failover; verify proxy refresh and realtime tests.

## 2. Continuity and cache convergence

- [x] 2.1 Preserve sticky and HTTP-bridge ownership for `REAUTH_REQUIRED` while retaining deactivation cleanup; verify repository and sticky-session tests.
- [x] 2.2 Treat committed `REAUTH_REQUIRED` snapshots as routable and clear legacy local overlays after invalidation convergence; verify cache-invalidation integration tests.
- [x] 2.3 Permit `REAUTH_REQUIRED` owners on HTTP bridge and realtime-live paths while preserving hard-owner and deactivation checks; verify bridge and realtime unit tests.

## 3. Adjacent account surfaces

- [x] 3.1 Align API-key pools, usage identity, reset-credit operations, probes, warmup, and rate-limit summaries with request routability; verify affected unit and integration suites.
- [x] 3.2 Permit automation dispatch for `REAUTH_REQUIRED` while keeping hard-unavailable statuses excluded; verify automation service tests.
- [x] 3.3 Include fresh `REAUTH_REQUIRED` accounts in dashboard weekly pace totals; verify dashboard overview integration tests.

## 4. Verification

- [x] 4.1 Update refresh preflight tests to assert fresh-guard persistence and no-exchange peer adoption; verify `tests/unit/test_auth_manager.py` passes.
- [x] 4.2 Run all changed test files and confirm no failures.
- [x] 4.3 Run strict OpenSpec validation and the repository lint/type gate.
