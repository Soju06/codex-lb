# Verification — 2026-09-10

Initial verification base: `dda902de6`. PR branch rebased onto `d2b2e7784` before publishing.

- Broader pricing, version, scheduler, migration-helper, account/report rollup, and request-log API regression run: **223 passed**.
- SQLite migration suite: **43 passed, 8 skipped** (the skips are PostgreSQL-only cases).
- PostgreSQL 16 backfill and full migration suites: **54 passed**, using an isolated temporary container.
- Final focused catalog, scheduler, Codex-version, and snapshot-generation run: **48 passed**.
- HTTP source-stub integration checks: **3 passed** (models.dev success, LiteLLM fallback, both sources unavailable).
- CLI `upgrade head` followed by `check`: `current_revision=20260910_000000_request_logs_missing_cost_index`, `migration_policy=ok`, `schema_drift=none`.
- Ruff, targeted ty, actionlint, and `git diff --check`: passed.
- Wheel built successfully; pricing JSON, generated Codex version, and refresh scheduler are included.
- Strict OpenSpec validation and archive completed; main specs are synchronized.

Current bundle: 92 OpenAI token-price entries; stable Codex fallback `0.154.0`.

The backfill regression exercises raw and folded costs, account duplicate collapse, deleted rows, warmup filters, report conversation normalization, the live tail, already-zero costs, source-specific exclusions, retained aggregate contributions after raw pruning, rollback, and repeated execution. The request-log API exposes the repaired cost.

Existing SQLAlchemy expression-index reflection warnings occurred in historical SQLite migration tests; there were no failing assertions. No production database was modified and no deployment was performed. Runtime repair begins automatically after deployment. Raw history already pruned by retention cannot be reconstructed.

## PR preparation

- Whole-repository `make lint typecheck`: passed after tightening optional-result assertions in the new tests.
- Final catalog, scheduler, Codex version, generator, background-loop/browser harness, HTTP-source, and cost-backfill selection: **67 passed**.
- Frontend lint and typecheck: passed. Full `make ci` was stopped after the existing `dashboard-flow` coverage test reported a failure; the failing test passed in isolation (**1 passed, 4 skipped**). The full local CI gate is not claimed as passing.
- Before/after screenshots use the real request-log UI with seeded API responses: an Astra request with 100,000 input tokens (50,000 cached) and 10,000 output tokens changes from unknown cost to **$1.05**. These are fixtures, not production screenshots.

- After rebasing: focused regressions **67 passed** (8 unrelated PostgreSQL-only durability cases skipped); SQLite session and missing-cost index checks **87 passed**; `make migration-check` reports the intended head, policy OK, and no drift. Cost-backfill regressions are included in the ongoing PostgreSQL CI target.

## Pre-merge review follow-up

Merged current main (`43a45f786`) and addressed all five initial review findings: publish-only write credentials with non-persistent checkout authentication, independent failure backoff, tier-only long-context rate validation/selection, exact eligible-row index predicates, and bounded generator response reads. The daily workflow also regenerates and tests the settings reference. Reservation tests use fixed rates independent of the changing bundle.

Affected pricing, reservation, settings, scheduler, and generator tests: **183 passed**. SQLite backfill and index upgrade/downgrade: **4 passed**; the index plan additionally passes with a bound source-kind parameter. Whole-repository lint/type checks and actionlint passed. `make migration-check` reports the intended head, policy OK, and no schema drift. Full unit and PostgreSQL regression reruns were started separately; current-head GitHub CI/review gates are checked before merge.
