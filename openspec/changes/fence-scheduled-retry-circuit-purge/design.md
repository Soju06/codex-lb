## Context

See proposal.md for the race and issue boundary. Main already stores `updated_at_epoch`, `admission_generation` and `consecutive_failures`. A claim can advance only generation; lagging-clock failure merges can advance only count. Age checks alone do not detect these changes.

## Goals / Non-Goals

Prove the scheduled cleanup repository output with real database writes between selection and deletion. Keep the existing per-row SQL deletes and their retention predicate. This patch neither introduces receipt fields nor promises that an already-selected old generation remains protected indefinitely.

## Decisions

Capture the observation timestamp, admission generation, failure count and null-safe failure detail alongside row identity, then compare them in each delete. Keep deletion per row, avoiding new batching machinery and keeping bind count independent of selected batch size. After any miss, finish the selected batch and return the actual deletion count. Immediately selecting again would adopt the changed fence and undo the protection.

For example, a selected row at generation 3 and timestamp 100 stays alive if a replay advances generation to 4, even when timestamp 100 is still old enough to purge. Other unchanged rows in that batch may be removed.

## Risks / Trade-offs

A concurrent change can defer unrelated later batches until the next scheduled pass. That bounds the pass instead of chasing moving state. Existing age and tombstone rules continue to decide eligibility on that later pass.

The #1954 receipt protocol also changes this method. Compatibility must preserve its active-receipt predicates and receipt snapshot fences while retaining this patch's existing-column checks. Shared-file textual conflicts do not justify a runtime dependency or dropping either contract. The original candidate and its held policy decisions remain untouched during extraction.

## Migration Plan

No migration or configuration change. Existing rows gain conditional-delete protection immediately when this code runs. A code rollback returns to the prior cleanup behavior without changing data format.
