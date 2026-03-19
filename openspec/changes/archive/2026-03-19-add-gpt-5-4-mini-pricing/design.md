## Overview

Add `gpt-5.4-mini` to the shared pricing registry and keep the existing lookup path unchanged. The model already flows through the shared cost helper, so once the canonical price entry and snapshot alias are present, API-key settlement and usage summaries pick it up automatically.

## Decisions

- Use the published base rates from the model page: `$0.75` input, `$0.075` cached input, and `$4.50` output per 1M tokens.
- Treat `gpt-5.4-mini-*` snapshot slugs as aliases of `gpt-5.4-mini`.
- Do not add a region-aware surcharge field yet; the repo has no billing input that can distinguish regional processing from standard processing.

## Risks

- If OpenAI later publishes tier-specific or long-context pricing for this model, the pricing dataclass will need another field instead of reusing the standard rate.
- Regional-processing pricing is intentionally not modeled, so requests that should be uplifted by OpenAI's regional policy will still use the base rate until the billing model grows a region signal.

## Verification

- Add unit tests for alias resolution and cost math.
- Validate the change with `openspec validate --specs` after implementation.
