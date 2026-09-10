## Why

Root cause of the #1695 CI flake — `POST /v1/responses` intermittently returned 409 `bridge_instance_mismatch` on a **single-instance** deployment, four times across four unrelated PRs in three days.

When an upstream WebSocket closes cleanly, the retiring bridge session's teardown releases its durable row while the next request is already creating a successor session and claiming the same row. Two defects let that race corrupt the claim:

1. **The fence did not distinguish predecessor from successor.** A same-owner reclaim kept the owner epoch, so the retiring session's fenced `release_session` (carrying that same epoch) still matched after the successor's claim and closed the row out from under it. Historically the epoch was kept because reused sessions re-claimed; today a reused session renews instead, so every claim comes from a successor in-memory session and there is no caller that needs epoch stability across claims.
2. **The claim's write was not authoritative.** The update mutated ORM attributes, and SQLAlchemy omits fields whose values match the transaction's read. On SQLite (`with_for_update` is a no-op) a release could commit between the claim's SELECT and its write; the claim then wrote only lease/timestamp fields, the release's `owner=None, state=CLOSED` survived, and the post-commit refresh handed the claimant a closed, ownerless row — surfaced to the client as the 409.

## What Changes

- Every claim of an existing durable row advances the owner epoch, including same-owner reclaims, so the predecessor's outstanding fenced release/renewals no-op after the successor's claim.
- The claim's update is an explicit `UPDATE` statement that sets every ownership field unconditionally, so a write interleaved between the claim's read and its commit cannot survive into the claim's result.
- Foreign-claim rejection semantics (live DRAINING, fail-closed lookups) are unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: durable bridge claims fence out the predecessor session and are authoritative over interleaved writes.
