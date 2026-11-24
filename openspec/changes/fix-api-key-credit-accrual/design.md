## Decisions

1. The local budget uses the published [Codex credit rate card](https://learn.chatgpt.com/docs/pricing#token-rates), read 2026-10-30. The supported text-model mapping belongs only to `app/core/usage/credit_pricing.py`. It is independent of the dynamic USD catalog and performs no runtime network fetch. Credit prices do not determine included subscription allowance.
2. Integer units of 10^-8 credits retain every fraction of the supported rate card without per-request rounding. Public counters keep whole-credit integer units. Their presented remaining value can differ from precise remaining credit by less than one credit. At the physical integer ceiling, credit counters saturate and remain exhausted until reset, so later reservation releases cannot reopen quota after overflow.
3. The reservation is the durable owner of the admitted model and requested credit tier. Settlement uses those values with actual token counts, rather than repricing response-only aliases or reconstructing missing request context. Existing USD accounting keeps its served-model and tier semantics.
4. Fast/Priority uses 2 and supported Ultrafast uses 6, following [purchased-credit pricing](https://learn.chatgpt.com/docs/agent-configuration/speed), not included-quota multipliers. Unpriced models and tiers, including Flex, fail closed for credit rules. Model-less metadata retains zero-charge admission.
5. Historical reconstruction groups non-warmup logs by model and requested tier; absent requested tier means Standard. It includes reported usage on failed requests but refuses unpriced token-bearing history. The existing reset option starts a fresh counter. Matching-rule edits do not reprice existing usage.

## Migration and rollback

Pause and drain every application writer before changing counter units. The migration checks both converted tables before mutation, scales only credit rows, and writes the nullable reservation credit tier last in the DML transaction. Its presence fences conversion during bootstrap or rewound-ledger replay, so counters cannot be scaled twice. Old and new writers cannot run together. Oversized legacy values stop conversion instead of overflowing SQLite integers.

Downgrade also requires paused writers. It restores whole-credit units and drops the new internal tier column; fractions accrued after upgrade are discarded. Tests prove legacy-value round-trip, nullable reservation entries and refusal before partial conversion. No running deployment is part of this change.

## Validation

A synthetic regression fails on the unchanged base because reservation remains zero. Focused unit and SQLite integration tests cover fractions, cached input, durable tier basis, concurrency, release, reconstruction, integer API fields and migration. PostgreSQL and the complete upstream CI gate remain required before merge.
