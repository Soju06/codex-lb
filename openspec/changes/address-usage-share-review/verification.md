# Local verification — 2026-09-30

Baseline: published PR #2463 head `00dceaec9`, integrated locally with upstream `main@f8ffbac20` without committing or pushing. Working tree and index contain the follow-up, not a new published head.

## Passed

- Broad API-key/estimation/cache/auth-manager/usage-updater/migration/OAuth selection: **638 passed**, **9 PostgreSQL-only skipped**. One additional tooling assertion failed as described below.
- Streamed coded/code-less usage-limit refresh unit selection: **13 passed**; public streaming refresh regression: **1 passed**.
- Account mutation, automation, and balancer-refresh suites: **129 passed**, **3 existing obsolete-locking cases skipped**.
- Six affected frontend files: **78 passed**.
- `make lint`, `uv run --frozen ty check`, simplicity budgets, and `git diff --check`: passed.
- Strict follow-up change validation and all **67 stable OpenSpec capabilities**: passed.
- Actual migration topology: **264 revisions, one head**, one added revision descending from current main's head. Both landed convergence-parent upgrades, convergence-head upgrade, key preservation, constraint/index repair, downgrade/re-upgrade, and schema drift checks pass.
- Generic bus/upstream-route code and tests, `app/main.py`, topology checker, and overflow-retirement artifacts match current main exactly. Deferred generic invalidation work is preserved in `/tmp/codex-lb-retain-immediate-invalidation-bumps.patch` and in the original published commits.

## Commit-dependent tooling assertion

`test_base_ref_revisions_reads_the_graph_out_of_git` compares the migration graph read from committed `HEAD` with files on disk. It fails because the local edits deliberately remove old branch-local revisions and add main's convergence plus the new usage-share revision without a commit. This is not a passing full-suite claim. All other **23 topology-tool tests** pass. Rerun the unchanged assertion after an authorized commit; do not weaken the test or reintroduce migration exceptions.

## Outstanding

- Independent read-only local review session `64361813-59ea-4e89-bc5e-a318debbfa32` completed with no concrete actionable defects in the follow-up. The reviewer did not rerun the supplied test results and retained the feature/policy approval hold.
- No PostgreSQL instance exercised locally for this follow-up.
- No commit, push, new cloud CI, merge, or production operation performed.
- Maintainer feature approval and incomplete-telemetry policy decision remain blocking. The submitted fail-open contract is preserved, not newly approved.
- #1528 remains open with its own migration lineage. Recheck parent ordering and timestamp reservations against main before publication/merge; the selected new timestamp is distinct from its listed files.
- The independent retained-invalidation reliability change is deferred, not opened as a new PR in this session.

This change remains active and unarchived until the commit-dependent check is verified. Final local review is complete; new-head cloud gates remain untested.
