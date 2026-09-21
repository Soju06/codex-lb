# Verification — reset-credit review regressions

Verified locally on 2026-09-21. All eight original findings and the additional independently reproduced recovery edge cases are fixed. Final independent review found no actionable correctness findings in the reviewed scope. No commit, push, deployment or production mutation was performed.

## Finding coverage

| Finding | Fix | Product-path evidence |
| --- | --- | --- |
| R1: expired ORM objects stop workers | Detach verification payload before consuming session cleanup | Real scheduler discovers and consumes eight accounts with database-backed coordination; verification accesses queued credentials after session close and remaining workers stay alive |
| R2: transient write loses confirmed receipt | Owned cancellation-safe settlement, three bounded fresh-session writes, scoped invalidation | Dashboard, automatic and v1 consumes recover a first-write failure with one upstream POST and one confirmed audit; repeated cancellation finishes settlement and releases the claim |
| R3: manual request IDs reconsume one credit | Consult credit-level durable pin under the serializer; resolve unresolved aliases to the original upstream request | Stale upstream C1/C2 listing: R1 succeeds, R2 conflicts, R1 replays without another POST |
| R4: authoritative zero becomes positive | Cap reconciled item count by upstream availability | Dashboard consume followed by targeted summary retains authoritative zero despite stale item statuses |
| R5: unsampled capacity enters denominator | Exclude entries without a usage sample | Real mutation hook restores 100/100 (100%) with another unsampled 100-credit plan |
| R6: poll erases pending state | QueryClient-scoped reconciliation and generation/timestamp guards | Real Accounts and Dashboard hooks retain pending after four failed reads and ordinary refetch, resolve on fresh polling, and reject older in-flight responses |
| R7: targeted summary loses duplicate identity | Targeted identity-existence query using full-list normalization | Canonical and trailing-slash summary routes match full-list duplicate, workspace, blank and placeholder classifications while full-list repository access is forbidden |
| R8: retry deferred past expiry | Shared expiry-aware retry time, persisted schedule restored and honored | Real automatic consume fails with 20 seconds left, retries before expiry with the original credit/request, and confirms; subsecond retry shrinking is prevented |

## Executed checks

| Check | Result |
| --- | --- |
| Initial expanded SQLite backend batch | 201 passed |
| Final independent backend batch after all fixes | 170 passed |
| Independent adversarial recovery tests | 19 passed: manual/no-body/automatic/v1 replay, cancellation, alias retention, clock skew |
| Final PostgreSQL regression suite | 14 passed: real scheduler/session lifecycle, settlement, retries, canonical replay, retention/purge and skew |
| Expanded frontend suite | 230 passed before the final dialog identity adjustment |
| Final independent frontend suites | 56 passed, including dialog retry and pending/poll reconciliation |
| Final browser flow | 1 passed: one consume, no mutation-driven list reload, pending state, updated count, selection/scroll preserved |
| Historical migration and corrected transient-error fixture | 6 passed |
| Explicit eventual alias-group purge | 1 passed on SQLite; included in final PostgreSQL suite |
| Ruff, TypeScript build, changed-file ESLint, scoped Python types, whitespace | Passed |
| Cancellation-safety and proxy-architecture scripts | Passed |
| Strict change and reset-credit main spec validation | Passed |
| Full main-spec validation | 50 passed / 15 pre-existing failures, unchanged |
| Whole-repository Python type check | One unrelated existing error in `tests/integration/test_proxy_chat_completions.py:109`; reset-credit scope passes |

The initial PostgreSQL batch passed 51 tests and failed two intentionally SQLite-specific claim tests because they compile SQLite upserts against PostgreSQL. Those tests pass in the SQLite batch and are outside PostgreSQL validation. Later PostgreSQL runs used the applicable regressions and all passed. Some early SQLite test-process shutdowns emitted aiosqlite worker warnings after event-loop close; the final independent 170-test run passed without assertion failures.

All PostgreSQL tests used a disposable PostgreSQL 18 container, separate localhost port and test database. The container was stopped and removed. Production containers and runtime state were not changed.

## Independent review and additional fixes

Original findings: `/tmp/codex-lb-reset-independent-review/review.md`.
First follow-up: sessions `01a0c21b-3d3f-7643-9918-954ea406c6f3` / review context `01a0c21b-3d93-7072-97f2-bb177e8ebcb5`; result `/tmp/codex-lb-reset-fix-review/result.txt`.
Final follow-up: sessions `01a0c228-ee82-75a2-b260-d6e0bfd7036b` / review context `01a0c228-eee1-7453-9843-2ec07f1f674d`; result `/tmp/codex-lb-reset-final-review/result.txt`.

Independent reproductions also exposed partial restore cleanup, past persisted retry timing after a failed preflight, fresh paused-account polls being overwritten, dialog dismissal/page reload recovery, alias TTL and clock skew. The fixes and corresponding product-path regressions are included in this change. Reviewers reran the reproductions against the updated code. Final verdict: no actionable correctness findings remain in the reviewed scope.

New explicit client IDs are durably bound to the unresolved credit and resolve to its original upstream identity. R1 can fail, R2 can recover through upstream R1, and replaying either after C1 disappears returns its confirmed receipt without consuming C2. Alias pins use origin `alias`; canonical selection prioritizes non-alias pins. Reads and purge retain the account/credit group while any pin remains inside the 24-hour window. An older canonical timestamp or slower replica clock cannot create a second upstream identity. The group becomes eligible for purge once all pins expire.

## Practical limits

The prior 100-credit/five-minute synthetic benchmark assumes ten seconds total per pre-consume fetch plus POST and mocks automatic redemption. It does not establish production throughput under every upstream retry/timeout budget. The real-session regressions close the lifecycle coverage gap without making an unconditional burst-capacity guarantee.

Settlement retries only database persistence, never a confirmed upstream consume. Process death or a database outage beyond bounded settlement can still lose the received result; no confirmation is fabricated. Deployment/runtime observation and GitHub readiness remain separate operator actions.

## Completeness and coherence

All 11 implementation/verification tasks are complete. Added requirements and the modified retention contract are synchronized into the two owning main specs, with stable rationale in their context documents. No remaining critical verification issues were found. Baseline whole-repository type/spec failures are recorded above rather than claimed green.
