## Context

See proposal.md for motivation. The fork is based on beta.5, with deployed API-key usage groups and uncommitted verified backports. Beta.6 has its own migration lineage and moves additional transport interpretation into the native helper. The two native implementations differ in queue limits and diagnostic metadata.

## Goals / Non-Goals

**Goals:** Preserve externally visible fork contracts while integrating the frozen upstream release and obtaining a single upgrade head.

**Non-Goals:** Integrating later upstream main commits, changing production state, rewriting deployed migrations, or publishing commits.

## Decisions

1. Perform a real no-commit merge against the pinned beta.6 commit. Preserve tracked work and the backport tests in a recoverable stash before merging; leave unrelated installer artifacts alone. Selective cherry-picking would omit requested release changes.
2. Keep fork byte-budget queues for WebSockets and upstream bounded queues for HTTP. Combine upstream response-interpretation fields with fork failure-phase metadata, and preserve fair scheduling and overflow cleanup. Choosing either entire file would discard an independent contract.
3. Adopt the upstream leader-election abstraction while retaining UTC+7 daily-reset alignment. Preserve key-dashboard grouping and route behavior through the upstream frontend and backend changes.
4. Add a no-op merge revision joining the deployed group migration and upstream report-rollup migration. Do not reparent an applied migration. Validate upgrades from both existing heads and a fresh database, retaining group data and indexes.
5. Restore backports by comparing against the merged release: retain additional sanitation and keyed burst settlement ordering, but do not undo beta.6 refactoring or duplicate fixes already shipped upstream.

## Risks / Trade-offs

- Semantic conflicts in automatically merged code → test native transport, routing, keyed retry/settlement, reset windows, and dashboard behavior in addition to resolving textual markers.
- Two migration branches → explicit merge revision and upgrade/data-preservation tests.
- Old native executable or dependency environment → use frozen locks and rebuild the helper before integration tests.
- Large imported test suite → run focused regressions first, followed by broad feasible checks; record exact coverage and any limitations.

## Migration Plan

This work prepares and verifies source only. A separately authorized deployment uses the existing HA surge workflow. Schema upgrades are additive and preserve usage groups during mixed-version operation. Do not perform a production downgrade or rollback as part of this change.
