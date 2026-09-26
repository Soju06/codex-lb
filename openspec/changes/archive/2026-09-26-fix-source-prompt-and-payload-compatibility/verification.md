# Verification: source prompt and payload compatibility

## Scope

The operator authorized fixes for the four findings from the fresh custom-source review. The implementation changes only source reference extraction, original-body selection, effective prompt-variable file checks, and direct-source `top_logprobs` classification. Existing unrelated workspace changes are preserved. No commit, push, production deployment, live provider call, or production database mutation is part of this task.

## Requirements and scenarios

All four delta requirements and ten scenarios are synchronized to the main source-routing spec. Stable rationale, concrete prompt/anchor examples, old-reference behavior, and multi-backend rollout constraints are in the source-routing context.

| Requirement | Implementation | Maintained regression evidence |
| --- | --- | --- |
| Prompt template ownership | `OwnershipScope.request_keys` adds the prompt domain; existing durable request-key publication and active/history lookups remain authoritative | Same/other/unknown prompt plus anchor; known/unknown/conflicting source override; streaming bootstrap followed by prompt-only continuity on a fresh application instance; historical ownership after active-row deletion; changed credential denial |
| Prompt variable files | Effective payload guard checks direct input-file/image variables before candidate admission and again before source dispatch | Client files with one/two sources and both routes; real subscription file pin; no new reservation; sediment image references; bad override with a safe peer; unchanged text/inline/URL values and malformed-type safety |
| Original source state | Original keys use source forwarding serialization in both balanced dispatch and source-miss denial | Local compact marker plus external compaction accepted with one source and preserved upstream; known compaction denied after credential change or source deletion |
| Log-probability controls | Direct-source-only classifier accepts integer 0..20, excluding booleans | 0/5/20 with one/two sources on both route and trailing-slash forms; malformed-value denial; real local AsyncOpenAI streaming failover and released/finalized reservations; subscription replay remains disallowed |

The new integration file has 61 route cases. The new unit cases explicitly distinguish direct-source classification from subscription-overflow replay.

## Fail-before evidence

Before loading the app fixes, the maintained subset reproduced **30 failures and 2 passing controls** in 81.94 seconds: mixed/unknown prompt ownership, prompt-variable file forwarding, compacted-history rejection, and `top_logprobs` source-count transitions. Log: `/tmp/source-prompt-fix-before.log`.

## Completed validation

- Isolated PostgreSQL new-route and portability suite: **153 passed in 362.58 seconds**. This includes fresh backend application instances sharing database ownership, streaming bootstrap, history-only continuity, token replacement, both route forms, trailing slashes, and SDK streaming. Log: `/tmp/source-prompt-fix-postgres.log`.
- Ruff across the repository: passed. Format check: all 1,157 Python files already formatted.
- Scoped app and edited/new test type checks: passed.
- Full-tree type check: the same six existing diagnostics remain in `test_model_source_pool_safety.py`, `test_proxy_chat_completions.py`, and `test_source_ownership_storage.py`; those files are unchanged by this task.
- Proxy architecture, timing seams, cancellation safety, and whitespace checks: passed.
- Strict OpenSpec validation: active change valid; **65 main specs passed**.

## Final regression and review results

- Broader maintained SQLite pool/ownership/reference/forwarding regressions: **146 passed, 2 PostgreSQL-only cases skipped in 387.66 seconds**. This includes existing settlement, retry bounds, cancellation, source availability, original file/subscription precedence, conversation/container/vector-store ownership, and candidate isolation. Log: `/tmp/source-prompt-fix-regressions.log`.
- Isolated PostgreSQL ownership-storage, history-storage, and existing fresh-replica reference follow-up: **30 passed in 189.20 seconds**. Both SQLite-skipped concurrency cases passed here. Log: `/tmp/source-prompt-fix-postgres-storage.log`.
- Independent frozen-checkout review: **no actionable findings in the four scoped fixes**. The reviewer passed **26 isolated SQLite route/SDK probes and 3 portability cases**, including prompt/compaction original-model versus fallback scope, identical versus replaced credentials with history-only state, malformed candidate-local prompt variables, file overrides before admission, original input-file precedence, retry cap, and no replay after stream handoff. Two initial failures came from closing an in-process replica setting shared test readiness to draining; correcting the probe harness resolved them without an app change. Log: `/tmp/source-prompt-fix-independent-review.log`.
- All three app files and both maintained test files match the independently reviewed snapshot byte-for-byte. Existing dirty workspace files outside the declared scope remain unchanged.

## Final assessment

Completeness: 9/9 tasks, 4/4 requirements, and 10/10 scenarios verified. Correctness: all maintained fixes/regressions passed on their applicable database, and independent review has no unresolved actionable finding. Coherence: the fix reuses durable ownership/history and candidate guards, preserves subscription policy, and introduces no settings or migration. Specifications/context are synchronized; ready to archive with the explicit type-check limitations below.

## Validation limits

Tests use synthetic local upstreams and an isolated PostgreSQL container. No claim is made about real-provider end-to-end behavior, a production rollout, a full repository test run, or a completely clean full-tree type check. The fix requires all production backends to run the updated guard; old binaries cannot enforce it during a mixed-version rollout. No schema migration or manual state change is needed.
