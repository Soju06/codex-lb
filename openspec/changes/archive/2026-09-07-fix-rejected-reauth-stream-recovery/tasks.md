## 1. Reproduction

- [x] 1.1 Reproduce expired-token plus permanent forced-refresh failure through the HTTP Responses route, including a second independent message.
- [x] 1.2 Add controls for successful refresh, repeated 401, hard ownership, opaque input, and post-output failure.

## 2. Implementation

- [x] 2.1 Persist access-authentication invalidation with credential-generation guards and honor it in selection and routing snapshots.
- [x] 2.2 Add bounded account-neutral replay for pre-visible authentication recovery and preserve the original terminal error otherwise.
- [x] 2.3 Verify lease release, settlement-before-health ordering, stale repair guards, and routing restoration.

## 3. Verification

- [x] 3.1 Run focused unit/integration regressions, lint, type checks, architecture/timing checks, and strict OpenSpec validation.
- [x] 3.2 Review the final diff and prepare a focused upstream-main PR with related-PR coordination notes.

## 4. PR Review Follow-up

- [x] 4.1 Merge current upstream main into the PR branch without changing the local bundle.
- [x] 4.2 Cover concurrent credential repair around the guarded status write and prevent stale local routing exclusion.
- [x] 4.3 Reconcile earlier routing requirements and context with the refresh-warning/access-rejection split.
- [x] 4.4 Remove the fixed future expiry and short lease-expiry test timing assumptions.
- [x] 4.5 Verify the combined changes and sync canonical specs before archival.

### Review Verification

- Combined routing, retry, bridge, authentication, cache, and multi-replica suites: 2,918 passed, three existing skips.
- Repair-before-write and repair-after-write regressions cover proven authentication rejection and deactivation; restoring unfenced publication reproduces both after-write failures.
- Cache/poller tests cover repair snapshots and pre-write snapshots, including post-write invalidation convergence and bridge reuse.
- The lease-expiry regression passes with an injected 250 ms follower delay that reproduced the original CI failure; the full multi-replica module passes.
- Lint, formatting, type checks, architecture, cancellation, and timing checks pass. OpenSpec 1.11.0 strict change validation and all 58 canonical specs pass.
