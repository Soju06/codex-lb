## Context

The shared pricing registry resolves exact model IDs first and otherwise selects the longest matching wildcard alias. Because GPT-5.6 has no pricing entries or specific aliases, its bare alias and all three personality slugs currently resolve through `gpt-5*` to the canonical `gpt-5` price.

OpenAI publishes four token prices per GPT-5.6 model and service tier: input, cached input, cache writes, and output. The existing `ModelPrice` and usage-token contracts represent input, cached input, and output only. They already support standard, Flex, Priority, and the published long-context multipliers used by GPT-5.6.

## Goals / Non-Goals

**Goals:**

- resolve the bare `gpt-5.6` alias to Sol and exact or suffixed personality slugs to the correct canonical price
- apply published standard, Flex, Priority, and long-context input/cached-input/output rates
- keep the existing lookup and cost-calculation paths unchanged

**Non-Goals:**

- represent or charge cache-write tokens
- add Batch-tier or regional-processing pricing
- change model discovery, request routing, or response usage schemas

## Decisions

- Add one `ModelPrice` per canonical personality slug. Sol uses standard `$5/$0.50/$30`, Flex `$2.50/$0.25/$15`, Priority `$10/$1/$60`, and long-context `$10/$1/$45`. Terra uses standard `$2.50/$0.25/$15`, Flex `$1.25/$0.125/$7.50`, Priority `$5/$0.50/$30`, and long-context `$5/$0.50/$22.50`. Luna uses standard `$1/$0.10/$6`, Flex `$0.50/$0.05/$3`, Priority `$2/$0.20/$12`, and long-context `$2/$0.20/$9`. All prices are USD per 1M tokens.
- Use the published `272_000`-token threshold semantics: long-context rates apply only when input tokens exceed 272K. The existing Flex path derives its published long-context rates by doubling Flex input/cached input and multiplying Flex output by 1.5. Priority pricing remains independent of the long-context path because the published Priority table exposes a single set of rates.
- Map the bare `gpt-5.6` alias to Sol and add `gpt-5.6-sol*`, `gpt-5.6-terra*`, and `gpt-5.6-luna*` aliases. The specific mappings ensure bare and suffixed GPT-5.6 model IDs resolve before the generic `gpt-5*` fallback.
- Keep cache-write pricing out of this fix. Adding a price without a separately reported token count would create false precision; that requires a follow-up extension to the usage contract and cost breakdown.

## Risks / Trade-offs

- [Cache writes remain uncharged] → Keep the omission explicit in the proposal and PR, and handle it in a follow-up that can distinguish cache writes from ordinary input.
- [Published rates can change] → Keep rates isolated in the existing default pricing registry and protect every tier with focused regression tests.
- [Aliases match arbitrary suffixes] → Follow the registry's established snapshot-alias convention; longest-match selection prevents fallback to the generic GPT-5 family.
