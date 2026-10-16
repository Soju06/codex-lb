## 1. Background persistence

- [x] 1.1 Reproduce stalled burst drain through enqueue and the injected writer, record a failing test, then make the smallest scheduling fix that passes.
- [x] 1.2 Verify multi-operation fairness, ordered bounded batches, terminal completion and cancellation through public interfaces.

## 2. Verification and delivery

- [x] 2.1 Repeat pinned-base and candidate measurements with identical synthetic workloads and report percentiles, throughput, write count and limitations.
- [x] 2.2 Run affected tests, lint, type checks, architecture gates and strict OpenSpec validation; review exact base and candidate.
- [x] 2.3 Sync and archive verified requirements, publish one issue-linked standalone PR and record hosted state plus monitoring ownership.

PR: https://github.com/Soju06/codex-lb/pull/2303. The PR readiness task accepts continuing monitoring after the final SHA receipt. Initial hosted snapshot: ten passed, thirteen pending, no failures; mergeable with checks still blocking. No upstream merge or deployment claimed.

At the documentation repair checkpoint on September 10, 2026, head `230314296417cc206cbeb38c3c4c783f8f8d547c` had successful CI Required, OpenSpec and simplicity checks and `CLEAN` merge state. A cancelled release-guard attempt was superseded by successful same-head run `34444076829`. Three CodeRabbit documentation findings remained under repair. Task 2.3 covers publication and monitoring handoff; upstream merge and deployment remain outside its completion claim.

JustYannicc owns the PR. Repair task `01a08a47-92f6-7292-b81f-be66110e5b8c` owns checks through its current-head verification receipt. PR readiness task `01a08845-2df8-7a91-863f-ef41f2182056` retains subsequent monitoring after explicit handoff. Each new head requires fresh hosted verification.
