## Context

`gpt-6-astra` is present in the upstream Codex model catalog captured from the
live proxy on 2026-09-05 Asia/Tbilisi time (2026-09-04 UTC). codex-lb currently bootstraps GPT-5.6 models before
the first upstream refresh and has separate owners for pricing, catalog
metadata, and Cursor-style model label normalization.

## Goals / Non-Goals

**Goals:**

- Teach bootstrap model discovery about `gpt-6-astra` with the captured upstream metadata.
- Price `gpt-6-astra` requests consistently across standard, Priority/Fast, Flex/Batch, and long-context paths.
- Normalize known suffix labels for `gpt-6-astra` through the existing request-policy path.

**Non-Goals:**

- Add a cache-write pricing field; current `ModelPrice` cannot represent it.
- Change live deployment, account import, database schema, or upstream refresh behavior.
- Invent any GPT-6 personality or variant slug beyond the provided upstream `gpt-6-astra` entry.

## Decisions

- Mirror the GPT-5.6 bootstrap helper shape for Astra. This keeps Codex-native metadata behavior uniform and avoids a parallel catalog path.
- Add explicit per-tier prices instead of multipliers. The provided rates include a distinct long-context output rate and match the existing `ModelPrice` shape.
- Add `gpt-6` as a bare-family price alias because `gpt-5.6` already maps to its flagship personality; do not add a bootstrap `gpt-6` catalog slug.
- Add `gpt-6-astra` to the existing suffix-normalization base list. The suffix vocabulary remains unchanged and deliberately does not add `max` or `ultra` model-name suffixes.

## Risks / Trade-offs

- [Risk] Upstream may later publish a release-tagged catalog with small text changes. -> The bundled entry cites the captured upstream catalog source and live refresh remains authoritative.
- [Risk] Cache-write pricing is absent from cost reports. -> Record only representable rates and do not invent a schema field.
- [Risk] Bare `gpt-6` could be mistaken for a real catalog slug. -> Alias it only in pricing; bootstrap catalog remains the real upstream slug list.
