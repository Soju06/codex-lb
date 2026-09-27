## Context

Pricing already distinguishes ordinary input, cached reads and output, including
service tiers and long-context thresholds. Both external catalogs expose
cache-write rates. Request logs and reservation settlement currently lose the
corresponding upstream usage field.

## Decisions

Use `cache_write_tokens` at the upstream boundary and
`cache_write_input_tokens` in internal usage and request-log persistence.
Extend the existing `ModelPrice` fields with cache-write prices for the same
tier/context groups. Do not hard-code a surcharge for unrelated models.

Partition input into cached reads, cache writes and remaining ordinary input.
Keep existing cached-read normalization; clamp writes to the remaining input.
Include write cost in the existing input-cost component rather than adding a
dashboard feature. Absent writes remain zero and do not affect legacy requests.

Add nullable request-log and reservation counts. Historical rows retain NULL, because the
discarded count cannot be reconstructed. Preserve already recorded non-NULL
costs and settled key limits; do not guess historical usage.

Thread write counts through existing settlement paths without changing their
transaction, compare-and-swap or cancellation ownership rules.

## Risks

Counting writes as both ordinary input and cache writes overcharges requests.
Dropping the count before settlement leaves key limits inconsistent with logs.
Dropping it at persistence breaks missing-cost repair. Tests must cover all
three boundaries, tier/context boundaries and historical rows.

## Migration

Use the current single Alembic head. Verify upgrade/downgrade and historical-row
compatibility. An absent cache-write count must remain compatible with callers
and old rows.
