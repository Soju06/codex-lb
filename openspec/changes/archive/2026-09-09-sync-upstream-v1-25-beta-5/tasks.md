## 1. Pinned integration

- [x] 1.1 Validate this change strictly, merge pinned beta.5 without committing, resolve the six predicted conflicts, and verify MERGE_HEAD and preserved unrelated work.
- [x] 1.2 Verify #2150 with accepted hard-owner, soft replacement turn-state, output/affinity and settlement bridge regressions; record exact passing commands.
- [x] 1.3 Rebuild the candidate Rust helper and verify SSE negotiation/framing plus fork WebSocket buffer, fairness, diagnostic and cleanup tests.
- [x] 1.4 Verify overload isolation/error weighting and retained account quarantine through unit and integration routing tests.
- [x] 1.5 Apply #2078's quota-recovery runtime/tests without changing quarantine; verify exhaustion, post-block evidence and foreground/background multi-replica recovery regressions.

## 2. Follow-up assessment and integrated checks

- [x] 2.1 Evaluate #2173/#2143/#2078 from source and relevant regression contracts; record evidence and the apply/defer decision for each in context.md.
- [x] 2.2 Run broad backend unit/integration/simulation and relevant frontend/HA preservation checks; record failures and environment-limited cases without counting skips as passes.
- [x] 2.3 Run lint, types, architecture/timing/cancellation and Rust gates; distinguish baseline failures using evidence.
- [x] 2.4 Sync imported and integration delta requirements and stable context; run normal and strict canonical OpenSpec validation without waiving existing failures.

## 3. Handoff

- [x] 3.1 Write verification.md with source/helper identity, exact check results, residual risks, requirement coverage and later deployment prerequisites; verify no production changes or unintended files.
- [x] 3.2 Verify completion against artifacts and archive only this change if every required gate passes; otherwise leave it active with precise blockers.

The separately approved Purpose cleanup is verified and archived. The separately
scoped selection fix and final broad backend reruns pass. See verification.md
for exact results and retained baseline-failure evidence. No commit, push or
deployment is included in completion of this change.
