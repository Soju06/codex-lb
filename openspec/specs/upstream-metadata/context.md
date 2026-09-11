# Upstream metadata

Pricing is refreshed once per hour from [models.dev](https://models.dev/api.json), with [LiteLLM](https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json) supplying additional OpenAI token rates. No SDK dependency or API key is needed. Models.dev publishes USD per million tokens; LiteLLM publishes USD per token. Only OpenAI text-output/chat entries are imported. Audio, images, and operator-configured external model sources keep their existing billing paths.

Models.dev is preferred where present. Missing rate fields can be supplemented only when both sources agree on base/cached prices, the context threshold, and overlapping tier rates. Explicit context tiers are required: the legacy `context_over_200k` field is not enough to distinguish 200,000 from 272,000 tokens. Multiple thresholds are currently unsupported and are rejected instead of approximated. Cache-write token billing is outside this adapter: request logs currently retain input/output/cache-read usage, not separate cache-write counts.

The lookup order is the active validated catalog, the generated bundle, then the existing code defaults. Exact model IDs and dated snapshots precede legacy aliases. Unknown GPT-5 minor families stay unpriced rather than inheriting GPT-5 rates; this lets later catalog discovery repair their NULL costs. A partial refresh retains compatible last-good fields and models missing from the response. A full source outage retries after five minutes. The calculation path performs no network lookup.

`pricing-cache.json` and `codex-version-cache.json` live inside the existing data directory. Writes use an atomic replace. Pricing snapshots include an update timestamp; a newer bundled snapshot wins over an older disk cache for overlapping models. Invalid cache files are ignored. Codex release versions are also restored on startup, with the configured fallback as the minimum persisted version. A stale source cannot downgrade an already resolved version. Stable GitHub release names/tags are preferred, with npm as the secondary source.

## Backfill

On startup, after new pricing arrives, and on hourly rescans, the scheduler fills NULL costs in retained subscription request logs. It scans at most 200 eligible rows per five-second tick using a partial index. A cursor prevents unknown models from monopolizing a batch; it is safe to reset on restart because NULL is the durable idempotency marker. All replicas refresh their in-process pricing/version metadata; only the scheduler leader repairs database rows.

Each repair takes the existing fold-state lock and mirrors cost changes in the same transaction into account/key lifetime totals, hourly usage, quarter-hour demand, and report totals. The hourly known-cost count increases too. Existing filters, deduplication, deleted-row dimensions, normalized conversation IDs, and the inclusive lifetime / exclusive hourly/report boundaries are preserved. A failure rolls back both raw and aggregate writes. Already populated costs, including zero, and API-key admission/limit counters are never retroactively rewritten.

For example, an unpriced Astra request with 1,000 input tokens (500 cached), 100 output tokens, and the [September 10 standard rates](https://developers.openai.com/api/docs/pricing) gains $0.0105 in its raw row and every applicable folded aggregate. A repeated pass adds nothing. Existing aggregate contributions from raw logs already pruned by retention remain intact. Such pruned requests cannot be accurately backfilled: the aggregates lack the per-request long-context/tier details needed to reconstruct their original costs.

## Bundled fallback maintenance

Run `uv run python scripts/update_upstream_metadata.py` to refresh the pricing snapshot and generated stable Codex version. Generation requires both pricing sources and a valid live version; runtime may degrade to one source. Unchanged pricing preserves its timestamp to avoid empty daily updates.

The `Update upstream metadata` workflow runs daily at 06:23 UTC or manually. It uses the repository's existing `RELEASE_PLEASE_TOKEN`, updates one maintenance branch without force-pushing, runs catalog/version tests, and opens a normal reviewable PR. It does not merge automatically. A merge conflict stops the workflow for resolution rather than overwriting review changes. No new `CODEX_LB_*` setting is required.
