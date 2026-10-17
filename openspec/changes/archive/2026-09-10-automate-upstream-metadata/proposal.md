## Why

A hardcoded pricing table misses new models such as GPT-6 Astra and leaves historical request costs NULL. Codex release lookups already run on every replica, but their last successful value is lost on restart and the bundled fallback still needs manual edits.

## What Changes

- Refresh validated OpenAI text-token prices from models.dev's public JSON, supplemented by compatible LiteLLM tier data, retaining last-good disk and bundled fallbacks without request-path network I/O.
- Generate the bundled price snapshot and stable Codex version from their public sources with one command and a daily update workflow.
- Repair only missing retained request costs in bounded, idempotent transactions, mirroring corrections into all affected folded cost measures.
- Persist successful Codex version lookups, recognize stable release tags, and back off failed lookups.

## Impact

Pricing, background lifecycle, request-log cost aggregates, and development automation. No required configuration or external account. Existing non-NULL costs and API-key reservation/limit counters are preserved. Raw history already pruned by retention cannot be reconstructed from aggregate token totals.
