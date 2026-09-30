# Local verification — 2026-09-30

Initial verification baseline: published PR #2463 head `00dceaec9`, integrated locally with upstream `main@f8ffbac20` before committing or pushing. The results below record that initial snapshot; post-commit verification follows.

## Passed

- Broad API-key/estimation/cache/auth-manager/usage-updater/migration/OAuth selection: **638 passed**, **9 PostgreSQL-only skipped**. One additional tooling assertion failed as described below.
- Streamed coded/code-less usage-limit refresh unit selection: **13 passed**; public streaming refresh regression: **1 passed**.
- Account mutation, automation, and balancer-refresh suites: **129 passed**, **3 existing obsolete-locking cases skipped**.
- Six affected frontend files: **78 passed**.
- `make lint`, `uv run --frozen ty check`, simplicity budgets, and `git diff --check`: passed.
- Strict follow-up change validation and all **67 stable OpenSpec capabilities**: passed.
- Actual migration topology: **264 revisions, one head**, one added revision descending from current main's head. Both landed convergence-parent upgrades, convergence-head upgrade, key preservation, constraint/index repair, downgrade/re-upgrade, and schema drift checks pass.
- Generic bus/upstream-route code and tests, `app/main.py`, topology checker, and overflow-retirement artifacts match current main exactly. Deferred generic invalidation work is preserved in `/tmp/codex-lb-retain-immediate-invalidation-bumps.patch` and in the original published commits.

## Post-commit verification

Implementation commit `1993b220e` integrates current main without rewriting the published history. After that authorized commit, all **24 migration-topology tests passed**, including the unchanged HEAD-to-disk graph assertion. Its earlier failure was caused by the deliberately uncommitted graph edits, not a product defect; no test or migration exception was weakened. `make lint` and `uv run --frozen ty check` also passed again.

The prior broad test result remains **638 passed / 9 skipped / 1 commit-dependent assertion failed** for its original snapshot; the newly passing assertion is recorded separately rather than presenting that earlier run as all-green.

## Outstanding

- Independent read-only local review session `64361813-59ea-4e89-bc5e-a318debbfa32` completed with no concrete actionable defects in the follow-up. The reviewer did not rerun the supplied test results and retained the feature/policy approval hold.
- No PostgreSQL instance exercised locally for this follow-up.
- Implementation commit is recorded above. Publication and current-head cloud checks remain separate from these local results; no PR merge or production operation is authorized.
- Maintainer feature approval and incomplete-telemetry policy decision remain blocking. The submitted fail-open contract is preserved, not newly approved.
- #1528 remains open with its own migration lineage. Recheck parent ordering and timestamp reservations against main before publication/merge; the selected new timestamp is distinct from its listed files.
- The independent retained-invalidation reliability change is deferred, not opened as a new PR in this session.

All implementation tasks and the commit-dependent local check are verified. Keep this follow-up active while maintainer policy approval and new-head cloud review/CI remain outstanding; local verification is not permission to merge.

## Reservation-failure review follow-up — 2026-09-30

Review thread: https://github.com/Soju06/codex-lb/pull/2463#discussion_r4143379876. Baseline: `fea4e88a2`. The receive loop already handles `AppError`; preparation creates neither a pending log nor an upstream lease, and API-key enforcement already rolls back refused limit writes. The concrete defect reproduced here is missing terminal logging/cleanup of the prepared, unreturned state, not a proven leaked database reservation.

- Before the source fix, all **8 public-route regressions failed** because the accepted WebSocket emitted its correct refusal but persisted **zero** terminal error logs. A test-harness timeout argument was corrected before recording this baseline. Log: `/tmp/codex-lb-2463-reservation-baseline.log`.
- After the fix, all **8 cases pass**: both public Responses WebSocket routes, initial/reused upstreams, real exhausted fixed-limit and revoked-key reservation failures, persisted account-neutral logs, cleanup-before-logging, unchanged client status/code/message, no upstream refusal frame, no orphaned reservation, healthy account, successful next turn, and exactly-once reservation settlement. Mocking policy refresh reproduces a mutation racing that refresh; reservation enforcement and request-log persistence are real.
- Full WebSocket integration, model-source guard, API-key usage/share/virtual-time, terminal cancellation, WebSocket auth, and proxy utility selection: **1699 passed**. Log: `/tmp/codex-lb-2463-reservation-suite.log`.
- A final typing-only non-null assertion in the test fixture was followed by another **8 passing** public regressions. The unchanged migration-topology suite separately passed **24 tests**. An initial combined `-k` command deselected those topology tests; the recorded 24-test result comes from the subsequent unfiltered run.
- `make lint` (including architecture, cancellation, timing, settings, and unchanged topology guards), `uv run ty check`, `.github/scripts/check_simplicity_budgets.py`, strict change validation, all **67 stable specs**, exact delta/stable admission-spec equality, and `git diff --check`: passed. One attempted simplicity invocation used the wrong path; the actual repository checker above was subsequently run successfully.
- Independent read-only review session `ea4ee201-2d54-4494-af7a-e116addf6878` (`openai-codex/gpt-5.5`): **no actionable findings**. Log: `/tmp/codex-lb-2463-reservation-independent-review.jsonl`.

Only reservation domain-refusal finalization changes in production. Successful preparation, reservation placement, usage-share policy, migrations, and account health are unchanged. Stable admission requirements/context are synchronized. The previously extracted invalidation work is now independently published as #2545; it is not reintroduced here.

Publication and exact-new-head cloud review/CI remain separate from these local results (task 4.4). The feature and incomplete-telemetry policy holds remain in force. No merge, archive, or production operation is authorized.
