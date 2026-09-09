## Context

See proposal.md for the approved scope. Baseline is `7982375e`; the working tree has only unrelated untracked installer notes. The pinned target is `b5469646`, not the moving upstream main. A read-only merge preview identified six conflicts, principally native imports and specs/settings documentation.

## Goals / Non-Goals

Produce a locally verified beta.5 integration while retaining fork contracts. Do not create a commit, branch, PR, deployment, or production data change. Do not turn evaluation of post-release fixes into an unbounded main integration or change transport/configuration defaults.

## Decisions

1. Use a non-committing merge of the pinned release on the current branch. This retains related Rust/Python protocol, tests and dependencies and preserves provenance for a later operator-requested commit. Resolve conflicts with combined contracts, not wholesale ours/theirs selection.
2. Verify #2150 at the bridge surface first: accepted hard-owner replay must not exclude its sole eligible owner; legitimate account replacement must drop the old turn-state; output, file pins, single lifecycle and API-key settlement guards must remain intact.
3. Keep fork `ByteQueue` connection/helper budgets, diagnostic metadata and bounded-batch fairness. Import the native SSE protocol capability together with the rebuilt helper. Test both SSE and WebSocket paths, including cleanup and bursts.
4. Evaluate #2173/#2143 against the fork, which already removed the 64-event HTTP limit and yields every 32 helper events. Retain that implementation; defer an HTTP-only memory budget instead of overwriting WebSocket budgets. Include #2078's independently reviewed quota helper/state-builder changes with their original multi-replica regressions, retaining all quarantine differences and omitting its unrelated service import formatting. See context.md for the evidence and residual risk.
5. Freeze dependency lockfiles and use isolated test data/env-file discovery. Do not point any tests or migrations at production. No Alembic files change in the release range.
6. Sync upstream-owned delta requirements without mass-archiving their changes. Keep integration-specific requirements in this change, rationale in context, and rendered docs linked to their owning specs. Existing global strict Purpose placeholders are not waived or fixed through unrelated scope expansion.

## Risks / Trade-offs

- New overload isolation changes account selection even when no textual conflict occurs: run routing, quarantine, ownership and settlement regressions.
- A stale native binary lacks the SSE capability: build and test the exact candidate helper, never substitute the running production binary.
- Existing HTTP unbounded buffering is distinct from upstream's old 64-event bug: record the remaining memory risk and any proposed follow-up explicitly.
- Upstream green CI does not verify the fork: record actual local checks and all failed, skipped or unavailable gates.

## Migration Plan

This local candidate requires no database schema migration. Production remains on beta.4. A later deployment requires explicit operator authorization, the HA deploy skill and script-owned surge workflow, image/helper identity checks, capacity/readiness checks and mixed-version review. Rollback is not implicitly authorized.
