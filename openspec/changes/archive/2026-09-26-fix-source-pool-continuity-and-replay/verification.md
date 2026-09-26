# Verification: source pool continuity and replay

Date: 2026-09-26. Scope: the review fixes for direct Responses source pooling. Other pre-existing installer, image, account warmup and isolation changes are outside this verification.

## Requirement coverage

| Requirement | Implementation | Regression evidence |
| --- | --- | --- |
| Balance authorized equivalent sources | `proxy/api.py` candidate selection and portability checks; existing `source_pool.py` | `test_model_source_pool.py`: five-key rotation, exact aliases, API-key scope, disabled/unsupported/saturated sources, both canonical routes and trailing slashes |
| Own and settle each failover attempt | `SourceDispatch`, portable-only retry gate, response publication cleanup | Pool tests: cleanup before retry, five-attempt bound, cooldown, credential replacement, reservation-release failure, timeout, disconnect and stream failure; review regression: JSON cancellation settles captured usage |
| Preserve credential ownership | `OwnershipScope.request_keys`, durable lookup in `api.py`, historical log fallback only without a durable owner | Safety tests: conversation bootstrap, encrypted replay, conflicting references, key scope, changed credentials, two application instances, failed duplicate publication; review regressions: replayed message/function-call IDs |
| Persist ownership before delivery | `SourceOwnershipRecorder`, conditional repository claims, migration, retention | Immediate follow-up before request-log completion; normalized delta reference before completion; failed publication withholds item/success; atomic conflicting claims and rollback; concurrent PostgreSQL refresh versus pruning; migration round trip and historical-row preservation |
| Reject Responses redirects without replay | `model_sources/forwarding.py` disables redirect following only for Responses | Safety tests: JSON and SSE 303 to unreachable result URL cause one POST and a 502; Chat Completions redirect behavior remains unchanged |

## Independent review

The follow-up review found five actionable issues: input item IDs were incompletely resolved, synthesized delta IDs were exposed too early, failed competing publications could poison log-based ownership, JSON cancellation lost captured usage, and PostgreSQL pruning could delete a concurrently renewed row. All are fixed and covered by route/storage regressions. The five new route cases and the PostgreSQL lock-race case failed before their fixes and passed afterward.

The final focused independent review reported no actionable findings remaining. Its reviewer inspected code and tests; test execution below was performed separately by the primary agent.

## Validation evidence

- Final PostgreSQL pool/safety/storage/review regression suite: **74 passed** (`/tmp/source-pool-fix-final-pg-suite.log`), including distinct application instances and synchronized concurrent renewal/pruning.
- Final SQLite routing/dispatch/alias regression suite: **291 passed** (`/tmp/source-pool-fix-final-route-regression.log`).
- SQLite safety/storage/review regressions: 24 passed, 1 PostgreSQL-only concurrency test skipped. That test passed on PostgreSQL.
- Forwarding unit and dispatch integration recheck: 163 passed after updating the HTTP test double for `allow_redirects` and preserving single-source reservation ordering.
- SQLite safety/storage/migration/retention checks before the final review additions: 40 passed.
- PostgreSQL ownership migration round trip: 1 passed. Independent CLI `upgrade head` followed by `check` reported `migration_policy=ok` and `schema_drift=none`.
- Ruff check and format check passed for all 17 touched Python files; scoped `ty check` passed.
- Proxy timing, cancellation safety and architecture guards passed.
- Strict change validation passed; strict main-spec validation: 65 passed, 0 failed.
- `git diff --check` passed. Existing unrelated worktree changes were preserved.

Temporary command logs are under `/tmp/source-pool-fix-*` and `/tmp/source-pool-review-*`; they are local execution evidence, not runtime dependencies.

## Design and operational checks

- Ownership uses shared database rows, scoped by client-key identity and public model. Only hashes are stored. Each write owns its session/transaction; cancellation waits for completion. No detached task or shared concurrent `AsyncSession` was introduced.
- Claims reject a different source or credential revision atomically. Every retained output item ID participates in continuity, including message/tool-call replay and normalized delta IDs. Durable records take precedence over accounting logs.
- The additive migration extends the current single Alembic head, including the already-pending account warmup migration. Existing logs are preserved; no ownership is invented for historical encrypted state. Downgrade/upgrade and schema drift were checked on SQLite and PostgreSQL.
- Production was inspected read-only: three application backends share PostgreSQL, and no public model currently has multiple enabled Responses sources. Complete the existing HA surge rollout on all backends before enabling a multi-key pool. Older binaries cannot provide the new ownership guarantee during a mixed-version rollout.
- Load, admission and cooldown state remain worker-local as specified; this change adds shared continuity, not a global concurrency limit. Unknown opaque pool state fails closed; single-source successful use can bootstrap external state.
- The isolated PostgreSQL 18 test container used no production volumes and was removed after verification. Production containers were only inspected read-only. No production configuration or data was changed. No commit, push or deploy was performed.

## Assessment

Completeness: 7/7 tasks complete and 5/5 changed requirements mapped to implementation and regression coverage. Correctness: all targeted checks passed. Coherence: implementation follows the shared ownership and replica-local scheduling design. No unresolved critical issue or warning remains. Main specs and context are synchronized; the change is verified for archive. This is local verification, not deployment or GitHub PR readiness.
