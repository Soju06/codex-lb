## 1. Metadata
- [x] Implement validated pricing import, bundled snapshot generation, and cached runtime refresh.
- [x] Persist stable Codex versions and automate bundled metadata updates.
## 2. Historical costs
- [x] Backfill missing retained subscription costs and mirror folded cost deltas atomically.
- [x] Schedule refresh and backfill with owned lifecycle and leader-gated database writes.
## 3. Verification
- [x] Cover source failures, rate tiers, restart fallback, backfill idempotence, and folded summaries.
- [x] Run focused tests, formatting/lint/type checks, and strict OpenSpec validation; sync and archive verified specs.
## 4. Pre-merge review follow-up
- [x] Address bounded source reads, publish-only credentials, and independent failure backoff.
- [x] Cover tier-only long-context prices and align the eligible-cost index with its query.
- [x] Keep generated settings docs and deterministic reservation fixtures in sync; verify the affected pricing, reservation, settings, scheduler, source-read, and index regressions.
