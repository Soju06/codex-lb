# Verification — reset-credit redemption reliability

Historical verification record from 2026-09-21. **Readiness withdrawn:** independent review subsequently reproduced eight defects. See the [follow-up verification](../2026-09-21-fix-reset-credit-review-regressions/verification.md) for fixes and current readiness evidence. Production deployment, commits, publication, and GitHub merge gates were not part of this task.

## Completeness and coherence

All twelve delta requirements have implementation evidence. The two owning main specifications and stable context are synchronized. Historical account-routing/proxy edits in the working tree are unrelated and remain untouched.

| Requirement group | Implementation | Regression evidence |
| --- | --- | --- |
| Discovery, one-hour window, deadline capacity | `app/core/usage/reset_credits_refresh_scheduler.py` | Scheduler tests: bounded discovery, generation rejection, earliest-expiry retry priority, entry into window between scans, shutdown cleanup, one-/three-replica burst benchmark |
| Durable outcomes, exact credit, retry, cross-replica serialization | `rate_limit_reset_credits/{api,outcomes}.py`, proxy reset route | Dashboard route lost-response replay, confirmed receipt replay, HTTP 200 unknown/no-reset, legacy pin reuse, expiry identity changes, three replicas consuming exactly once |
| Account-scoped invalidation | `rate_limit_reset_credits/{invalidation,store}.py`, application lifespan | Coalesced revisions, unchanged peers, generation fencing, source acknowledgement, restart baseline, deleted history, failed writes, legacy writer fallback |
| Targeted authenticated summary/freshness | `accounts/{api,schemas,mappers}.py` | Target-only repository reads, canonical/trailing slash, missing/pending-deletion account, password authentication, freshness |
| Accounts/dashboard/usage-panel reset UI and settings | Account mutation hooks, actions/card/table, translations | Targeted cache merge, quota totals, concurrent targets and partial failure, no false success toast, pending actions, bounded missing-snapshot reads, existing reset sorting, confirmation and request identity |
| Additive schema/legacy compatibility | Two `20260921` migrations | SQLite and PostgreSQL upgrade/downgrade/re-upgrade, legacy unknown backfill, schema drift, single-head graph |

## Executed checks

- Focused backend unit/store/scheduler/architecture set: **117 passed**. Cancellation-safety and proxy-architecture scripts also pass.
- Final SQLite dashboard/v1/replica/migration integration batch: **68 passed**. Additional explicit HTTP-200 outcome variants: **2 passed**.
- PostgreSQL route/replica set: **31 passed**, with one test-fixture timestamp failure corrected; focused rerun including that case, expiry guards, legacy retry, and authenticated summary: **11 passed**. Eight SQLite-specific claim/lease tests are intentionally excluded from the PostgreSQL run; PostgreSQL serialization is exercised by the three-replica consume test.
- PostgreSQL historical migration downgrade/re-upgrade: **1 passed**; SQLite migration coverage is included in the integration batch.
- Frontend expanded suite: **180 passed**, with one fixture-only aggregate expectation corrected; final hook rerun: **10 passed**, including the corrected aggregate case and four-read exhaustion without another consume. Pending card/table, Accounts, sorting, settings, and reset-dialog coverage included.
- Playwright real-browser flow verifies exactly one consume, no mutation-triggered account-list request, pending state, updated count, and preserved selected URL/list scroll.
- Ruff, targeted Python type checks, TypeScript build, changed-file ESLint, and `git diff --check` pass.
- Strict change validation passes. Strict main-spec validation remains **50 passed / 15 failed**, the same failures observed before this implementation. Committed HEAD originally has 49/16; the unrelated pre-existing account-routing edits explain the difference. Main frontend baseline has pre-existing non-normative requirements; this task does not silently claim full-repository validation is green.

## Burst and backlog experiment

Each synthetic replica has a 1,200-account discovery inventory. Every discovery fetch costs one simulated second, with injected timeouts; 100 known eligible accounts all expire in 300 seconds. A successful redemption costs **10 seconds total for pre-consume fetch plus consume**, and quota verification stalls. This does not mean each individual HTTP call costs ten seconds.

| Simulated replicas | Last of 100 consumes | Discovery duration | Discovery attempts | Discovery concurrency |
| --- | ---: | ---: | ---: | ---: |
| 1 | 254.85 s | 420.67 s | 1,200 | 3 |
| 3 | 259.71 s | 433.18 s | 3,600 | 9 |

The last successful consume includes queue wait; approximately 250 seconds can therefore be queue delay for the tail of this burst. Deadline work finishes before expiry even while discovery remains incomplete. Four deadline slots and one verification slot per replica are separate from discovery. The synthetic multi-replica scheduler test uses an in-memory serializer; separate SQLite and PostgreSQL integration tests use the real durable coordination path and prove only one upstream consume for one credit across three independent callers/stores.

This is a capacity regression, not an end-to-end production throughput guarantee. Cold-start discovery of never-observed credits, authentication failures, upstream throttling/outages, database latency, or more than ten seconds total per redemption can exhaust five minutes. Beginning normal automatic work one hour before expiry supplies additional margin. After deployment, use the discovery elapsed/count log and deadline queue-delay/attempt log to compare real latency against these assumptions.

## Visual evidence

Captured with synthetic fixtures, without production identities or credentials:

- Before: `/tmp/codex-lb-reset-credit-before/accounts.jpg`, `/tmp/codex-lb-reset-credit-before/settings.jpg` (isolated committed HEAD).
- After: `/tmp/codex-lb-reset-credit-after/accounts.jpg`, `/tmp/codex-lb-reset-credit-after/settings.jpg`.
- Reset transition: `/tmp/codex-lb-reset-credit-after/reset-pending.png`, `/tmp/codex-lb-reset-credit-after/reset-completed.png`.
- Persistent browser regression: `frontend/screenshots/capture.spec.ts`, “redeem one account keeps the list and shows pending reconciliation”.

Screenshots are local review artifacts and need attaching if a PR is later published. These historical checks did not establish correctness of the real scheduler/session path; the follow-up verification supersedes this readiness assessment. Deployment/runtime observation and GitHub readiness remain separate operator actions.
