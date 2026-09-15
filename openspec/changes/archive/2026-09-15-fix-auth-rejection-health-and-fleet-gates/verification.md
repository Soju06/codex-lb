## Review Scope

Review: https://github.com/Soju06/codex-lb/pull/2132#pullrequestreview-5203052603

Parent head: `1e851b93b4ee4d09e6c0aac8d8a982f631613665`.

All three findings are addressed: rejection staleness now depends on access material, late health writes preserve stored rejection, and fleet refresh uses the canonical access-aware prefilter and attempted count. The independent reviewer inspected the combined patch and reported no new actionable finding. PostgreSQL SQL compilation was inspected; these local regressions were not native PostgreSQL executions.

## Executed Verification

- Four refresh-only rotation/rejection cases failed before the fix. They now pass with exact or re-encrypted equal access, rotation before the initial CAS or after a retry read, bridge exclusion, and selection exclusion. Existing genuine access repair and operator veto cases remain covered.
- Late rate-limit/quota writes reproduced rejected accounts becoming rate-limited/quota-exceeded. Four final cases cover reset expiry and subsequent genuine repair, actual stored and caller state, peer routing snapshots, bridge reuse, and selection after success callbacks.
- The fleet HTTP regression failed with attemptedCount 1 instead of 3. It now exercises real usage refresh and persistence for usable/unknown-expiry warnings, excluding expired/rejected/paused/deactivated accounts. Only the upstream fetch is mocked.
- Combined rotation, rejection CAS, cache, multi-replica, token refresh claim, and fleet integration suites: 169 passed.
- Balancer/concurrency coverage passed in the initial combined run: 284 passed and 3 existing skips; only the two new tests' artificial clock-rewind expectations failed and were corrected to use monotonic scenarios. The corrected four cases pass in the final integration run above.
- Account repository, auth manager, usage updater, and automation checks: 242 passed initially with one stale repository mock expectation. The mock now returns the actual persisted-status contract, and all 17 repository lock tests pass.
- Full make lint typecheck passes. All 65 canonical specs and this change pass strict validation. git diff --check passes.

These test groups overlap and must not be summed as unique coverage.

## Publication Boundaries

Commit, push, and per-thread replies are authorized. New-head cloud CI remains to be checked after publication; prior green CI does not verify this patch. The previously reproduced rotation-commit/local-clear race remains outside this change and unresolved. PR ordering with #2117/#2391 remains a maintainer decision. This change does not modify the bundle worktree or restart the running service.
