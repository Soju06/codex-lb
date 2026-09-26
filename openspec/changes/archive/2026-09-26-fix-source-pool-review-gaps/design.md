## Context

See proposal.md. The existing tree contains substantial unrelated uncommitted work; preserve it. The seven confirmed review findings and twelve probes are recorded in `/tmp/source-pool-second-review-findings.md` and `/tmp/codex-lb-pool-second-review-31fioabx/tests/integration/test_second_review_probes.py`. Those local artifacts are evidence, not implementation dependencies. Production runs blue/green/amber behind HAProxy using shared PostgreSQL.

## Goals / Non-Goals

Goals: close all seven review gaps through public routes; preserve source-owned state across backends; separate initial request compatibility from replay eligibility; prove migration, concurrency and cleanup contracts.

Non-goals: global load/cooldown coordination, new settings, subscription routing redesign, unrelated installer/image/warmup changes, commits or deployment.

## Decisions

- Keep direct-source compatibility separate from overflow's restrictive portability policy. Add a focused direct-source decision using declared capabilities and neutral request controls. Explicitly retain fail-closed handling for opaque state; do not simply bypass all replay checks. Verify both native Codex and `/v1` routes with namespace tools and ordinary function tools.
- Resolve existing source ownership before treating a missing candidate as a subscription-routing opportunity. Preserve the established file/subscription precedence. A disappeared owner is an unavailable owned route, not an unowned model.
- Validate the effective forwarded reference set after request shaping/overrides and before sending upstream. Resolve each reference individually, preserving the original public-model scope across normalization. References introduced by overrides require evidence even with a single source. Evaluate each candidate independently so an unused source override cannot block a valid owner. Reject conflicting overrides rather than relying on ownership publication to discover mistakes after generation.
- Preserve existing encrypted credential bytes when a submitted token is unchanged, avoiding a new plaintext-derived secret field. Changed credentials still invalidate continuity; plaintext never appears in logs or stored ownership keys.
- Record unresolved call references in the same scoped ownership domain. Prove complete ordered, type-matched call/result history using the existing replay predicate before omitting bookkeeping IDs; partial calls and mismatched results retain their ownership keys. Ownership extraction and direct-source portability share that classification; bookkeeping IDs are removed only from the classification view, never the forwarded body.
- Reconcile legacy evidence before new response-ID claims. Shared transactions/uniqueness must prevent concurrent backends from assigning conflicting ownership. Existing losing publication logs must not poison successful ownership.
- Keep credential-version evidence for response-ID fallback for at least as long as the corresponding historical fallback can authorize a request. The implementation stores a compact append-only ownership history alongside the expiring live row: each successful claim records `(reference_key, source_id, source_revision)` with an idempotent uniqueness key, live rows remain the fast path and are pruned as before, and historical lookup is used when a live row is expired or pruned. New source request-log rows also record the credential revision. Existing log-only rows have no credential revision and are treated as unverifiable: they cannot be resumed or reconciled into a new claim, even if the current source ID matches. This deliberately fails closed rather than assuming the current token was used historically. Migration backfills history from extant live ownership rows and leaves pre-existing request-log revisions null. Plaintext credentials never enter ownership storage.
- Let the implementation agent select the smallest storage representation that satisfies these contracts, documenting the decision before schema edits. Keep one intended Alembic head and explicit data handling for pre-existing rows.

## Risks / Trade-offs

- Broader direct-source compatibility could enable unsafe failover: prove fresh namespace/neutral-control acceptance separately from replay denial for references and stateful tools.
- Legacy logs lack credential versions: preserve provable historical compatibility and decline unverifiable cases rather than guessing. Document the precise boundary and migration behavior.
- Concurrent claims and retention can race across backends: use database atomicity and test separate PostgreSQL sessions with synchronized conflicts; no shared `AsyncSession` or process-local ownership locks as cross-backend protection.
- Ownership writes add failure/cancellation points: retain bounded work, settlement-before-health ordering and awaited task cleanup, including partial failure.

## Migration Plan

Implementation and tests use isolated databases. Any schema change is additive and extends the current pending migration chain; run upgrade/check and downgrade/upgrade tests on SQLite and PostgreSQL. No production migration is run in this task. A later authorized rollout must use the existing HA surge script and finish all backends before enabling pooled keys; mixed-version ownership behavior is documented explicitly.

## Execution Plan

The primary agent owns planning, integration checks and final review. One requested `gpt-6-luna` implementation agent at `max` effort owns application/test edits, working from this change and the preserved dirty-tree baseline. It must not commit, push, deploy or mutate production. If that exact model cannot be selected, report the actual tool limitation and resolve the model choice before delegating dependent implementation. After implementation, run an independent adversarial review, fix confirmed findings within the authorized scope, verify/sync the spec and archive only after checks pass.
