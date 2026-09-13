## 1. Fix

- [x] 1.1 `claim_session` advances the owner epoch on every claim of an existing row, including same-owner reclaims
- [x] 1.2 The claim's update path writes all ownership fields through an explicit `UPDATE` instead of ORM attribute mutation
- [x] 1.2b The insert path builds its own snapshot too, so a concurrent advance cannot hand two claimants the same fence
- [x] 1.3 The update is a compare-and-set on the epoch read, so racing claims serialize instead of sharing a fence; the loser retries against fresh state

- [x] 1.4 Contended retries drop takeover permission, at the repository and at the service's claim retry
- [x] 1.5 A creator that has lost its inflight slot aborts before claiming; one that fails to register anyway closes its session without releasing the durable row

## 2. Tests

- [x] 2.1 Same-owner reclaim advances the epoch, and a predecessor release fenced on the old epoch no-ops (row stays ACTIVE and owned)
- [x] 2.2 Deterministic interleave reproduction: a release committing between the claim's SELECT and its write does not survive into the claim's result (fails on the pre-fix code)
- [x] 2.3 Racing successor claims land on distinct epochs (deterministic competitor injection)
- [x] 2.3b A CAS loser does not steal a foreign winner's live lease (fails without revalidation)
- [x] 2.3c The service's claim retry stops at a live foreign owner instead of restoring takeover
- [x] 2.3d A rejected creator does not release the registered winner's durable row
- [x] 2.3e A creator superseded mid-claim hands its epoch to the registered winner; unrelated rows are untouched
- [x] 2.3f A fenced renewal adopts a same-instance advance when registered, and still evicts when a different session holds the slot
- [x] 2.3g A replacement holding only the in-flight slot is protected; a sole creator still releases
- [x] 2.3h A newer process incarnation sharing the instance ID still fences the predecessor out
- [x] 2.3i Neither adoption nor handover crosses an account change; the session is evicted instead
- [x] 2.4 Route-level regression through POST /v1/responses: captive predecessor release lands late and is fenced out
- [x] 2.5 Existing claim/takeover suites pass unchanged (DRAINING rejection, account-change fencing, process-epoch semantics)

## 3. Spec

- [x] 3.1 Add the successor-fencing and authoritative-write requirement to `responses-api-compat`
