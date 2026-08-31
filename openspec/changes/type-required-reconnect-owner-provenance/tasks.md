## 1. Regression coverage

- [x] 1.1 Prove transient required file-owner saturation waits, retries the same
  owner, and can recover.
- [x] 1.2 Prove a deleted required file owner returns typed unavailable and maps
  immediately to the existing 502.
- [x] 1.3 Prove non-file previous-response owners remain ordinary required
  preferred accounts under dashboard single-account routing and API-key scope.
- [x] 1.4 Prove continuity-owner hard-affinity saturation retains its transient
  error code.

## 2. Implementation

- [x] 2.1 Scope new reconnect continuity provenance to live file pins while
  preserving the existing account-neutral path.
- [x] 2.2 Gate early required-owner mapping on confirmed account disappearance.
- [x] 2.3 Preserve transient hard-affinity saturation for bounded recovery.
- [x] 2.4 Preserve file-pin single-account behavior without bypassing API-key
  assignment scope.

## 3. Validation

- [x] 3.1 Run focused HTTP-bridge and load-balancer unit tests.
- [x] 3.2 Run Ruff, ty, architecture checks, and strict OpenSpec validation.
- [x] 3.3 Inspect the final branch diff against main.
