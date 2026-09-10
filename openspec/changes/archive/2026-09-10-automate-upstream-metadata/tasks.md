## 1. Metadata
- [x] Implement validated pricing import, bundled snapshot generation, and cached runtime refresh.
- [x] Persist stable Codex versions and automate bundled metadata updates.
## 2. Historical costs
- [x] Backfill missing retained subscription costs and mirror folded cost deltas atomically.
- [x] Schedule refresh and backfill with owned lifecycle and leader-gated database writes.
## 3. Verification
- [x] Cover source failures, rate tiers, restart fallback, backfill idempotence, and folded summaries.
- [x] Run focused tests, formatting/lint/type checks, and strict OpenSpec validation; sync and archive verified specs.
