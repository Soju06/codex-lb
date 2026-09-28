# Upstream metadata

Pricing is refreshed once per hour from [models.dev](https://models.dev/api.json), with [LiteLLM](https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json) supplying additional OpenAI token rates. No SDK dependency or API key is needed. Models.dev publishes USD per million tokens; LiteLLM publishes USD per token. Only OpenAI text-output/chat entries are imported. Audio, images, and operator-configured external model sources keep their existing billing paths.

Models.dev is preferred where present. Missing rate fields can be supplemented only when both sources agree on base/cached prices, the context threshold, and overlapping tier rates. Explicit context tiers are required: the legacy `context_over_200k` field is not enough to distinguish 200,000 from 272,000 tokens. Multiple thresholds are currently unsupported and are rejected instead of approximated. Cache-write rates use the same source, tier and context coherence rules as the other input rates.

The lookup order is the active validated catalog, the generated bundle, then the existing code defaults. Exact model IDs and dated snapshots precede legacy aliases. Unknown GPT-5 minor families stay unpriced rather than inheriting GPT-5 rates; this lets later catalog discovery repair their NULL costs. A partial refresh retains compatible last-good fields and models missing from the response. A full source outage retries after five minutes. The calculation path performs no network lookup.

`pricing-cache.json` and `codex-version-cache.json` live inside the existing data directory. Writes use an atomic replace. Pricing snapshots include an update timestamp; a newer bundled snapshot wins over an older disk cache for overlapping models. Invalid cache files are ignored. Codex release versions are also restored on startup, with the configured fallback as the minimum persisted version. A stale source cannot downgrade an already resolved version. Stable GitHub release names/tags are preferred, with npm as the secondary source.

## Cache-write accounting

Native Codex usage retains `input_tokens_details.cache_write_tokens` separately
from cached reads. Writes replace ordinary input rather than adding a second
input charge. For example, 100000 Astra write tokens at 12.5 USD per million cost
1.25 USD, not the ordinary-input 1.00 USD or the double-counted 2.25 USD.
The write charge stays in the existing input-cost breakdown component.
See [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
for the upstream token categories.

Existing cached-read normalization applies first; writes are limited to the
remaining nonnegative input. Missing writes count as zero. Malformed write
fields follow the same strict usage parsing as malformed cached-read fields.
If an applicable write price is unavailable, writes keep ordinary input pricing
rather than a guessed surcharge. Explicit external model-source pricing is
unchanged.

Request logs and finalized API-key reservations persist nullable
`cache_write_input_tokens`. Old rows remain NULL: their discarded write count
cannot be reconstructed from those rows. Cost-limit finalization uses the same partition and rates as
request costing, without changing reservation ownership or idempotency.
The additive migration leaves historical non-NULL costs and settled limits
untouched.

Automation compact pings, limit warm-ups and quota planner warm-ups are native
writers too; each persists the upstream write count. A keyed quota planner
warm-up also supplies that count when its reservation is finalized. Missing
counts remain unknown in request logs, while settlement treats them as zero.
Admission estimates continue to use their existing input/output budget:
changing that policy is separate from preserving observed usage.

## Backfill

On startup, after new pricing arrives, and on hourly rescans, the scheduler fills NULL costs in retained subscription request logs. It scans at most 200 eligible rows per five-second tick using a partial index. A cursor prevents unknown models from monopolizing a batch; it is safe to reset on restart because NULL is the durable idempotency marker. All replicas refresh their in-process pricing/version metadata; only the scheduler leader repairs database rows.

Each repair takes the existing fold-state lock and mirrors cost changes in the same transaction into account/key lifetime totals, hourly usage, quarter-hour demand, and report totals. The hourly known-cost count increases too. Existing filters, deduplication, deleted-row dimensions, normalized conversation IDs, and the inclusive lifetime / exclusive hourly/report boundaries are preserved. A failure rolls back both raw and aggregate writes. Already populated costs, including zero, and API-key admission/limit counters are never retroactively rewritten.

For example, an unpriced Astra request with 1,000 input tokens (500 cached), 100 output tokens, and the [September 10 standard rates](https://developers.openai.com/api/docs/pricing) gains $0.0105 in its raw row and every applicable folded aggregate. A repeated pass adds nothing. Existing aggregate contributions from raw logs already pruned by retention remain intact. Such pruned requests cannot be accurately backfilled: the aggregates lack the per-request long-context/tier details needed to reconstruct their original costs.

## Bundled fallback maintenance

Run `uv run python scripts/update_upstream_metadata.py` to refresh the pricing snapshot and generated stable Codex version. Generation requires both pricing sources and a valid live version; runtime may degrade to one source. Unchanged pricing preserves its timestamp to avoid empty daily updates.

The `Update upstream metadata` workflow runs daily at 06:23 UTC or manually. It uses the repository's existing `RELEASE_PLEASE_TOKEN`, updates one maintenance branch without force-pushing, runs catalog/version tests, and opens a normal reviewable PR. It does not merge automatically. A merge conflict stops the workflow for resolution rather than overwriting review changes. No new `CODEX_LB_*` setting is required.
