# GPT-6 request cost recognition

## Purpose and source

Add GPT-6 to the shared native pricing table used by request logs and API-key accounting. Rates were verified on 2026-09-23 against [OpenAI pricing](https://developers.openai.com/api/docs/pricing) and the [Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), [Sol](https://developers.openai.com/api/docs/models/gpt-6-sol), and [Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) model pages.

## Decisions and constraints

Use explicit family entries and the existing tier fields, with the Fast multiplier applied after context-rate selection. A broad GPT-6 fallback could silently charge an unrelated future family at the wrong price. No runtime configuration or network price fetching is needed. Cache-write billing, Batch processing, and regional surcharges require usage/routing contracts beyond this change.

## Example and boundaries

A standard Sol request with 200,000 input tokens (100,000 cached) and 100,000 output tokens costs `$0.20 + $0.02 + $1.00 = $1.22`. Fast costs `$2.44`; Flex costs `$0.61`. Total input, including cache hits, determines whether the request exceeds 272,000 tokens. At exactly 272,000, short-context prices still apply.

## Operational notes and failure modes

Deploying this code enables cost recognition for new requests and their quota settlements. Persisted historical costs and settled API-key counters are retained. Existing null-cost rows can gain calculated request-detail breakdowns, while historical aggregate totals remain based on persisted costs. No database migration or production data rewrite is included.
