## 1. Fix

- [x] 1.1 `claim_session` advances the owner epoch on every claim of an existing row, including same-owner reclaims
- [x] 1.2 The claim's update path writes all ownership fields through an explicit `UPDATE` instead of ORM attribute mutation
- [x] 1.3 The update is a compare-and-set on the epoch read, so racing claims serialize instead of sharing a fence; the loser retries against fresh state

## 2. Tests

- [x] 2.1 Same-owner reclaim advances the epoch, and a predecessor release fenced on the old epoch no-ops (row stays ACTIVE and owned)
- [x] 2.2 Deterministic interleave reproduction: a release committing between the claim's SELECT and its write does not survive into the claim's result (fails on the pre-fix code)
- [x] 2.3 Racing successor claims land on distinct epochs (deterministic competitor injection)
- [x] 2.4 Route-level regression through POST /v1/responses: captive predecessor release lands late and is fenced out
- [x] 2.5 Existing claim/takeover suites pass unchanged (DRAINING rejection, account-change fencing, process-epoch semantics)

## 3. Spec

- [x] 3.1 Add the successor-fencing and authoritative-write requirement to `responses-api-compat`
