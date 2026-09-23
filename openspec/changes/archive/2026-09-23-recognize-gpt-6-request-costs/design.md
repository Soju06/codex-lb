## Context

All native request-log and API-key cost paths share `app/core/usage/pricing.py`. GPT-6 models have no entry or alias, so lookup returns no price. Existing explicit priority rates return before long-context handling.

## Goals / Non-Goals

Goals: recognize all three published GPT-6 families, cached usage, supported service tiers, and context-length pricing consistently.

Non-goals: model catalog changes, unrelated model repricing, automatic price downloads, cache-write billing, Batch support, regional uplifts, or historical cost/quota backfills.

## Decisions

- Extend the existing price table and family aliases. Avoid a generic `gpt-6*` alias that would misprice new families.
- Represent GPT-6 Fast rates with the existing `priority_multiplier=2.0`; apply that multiplier after choosing standard short/long-context rates. Keep explicit priority-rate overrides authoritative so existing model behavior is preserved. This avoids new model-price fields or model-name conditions.
- Use existing Flex fields and context multipliers. No persistence schema changes are required.
- Verify calculations plus public proxy settlement and request-log API output; helper-only tests would miss a broken cost consumer.

## Risks / Trade-offs

- Published prices change: record the source and verification date in context.
- Existing logs retain their persisted totals and quota counters. Null-cost request detail breakdowns may become calculable, but historical aggregates are not backfilled by this patch.
- Cache-write usage is not represented in the current usage model; this patch covers existing input, cached-input, and output accounting only.
