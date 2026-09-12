## 1. Pricing table

- [x] 1.1 Add the `gpt-6-astra` entry with standard, Fast/priority, Flex, and standard long-context rates.
- [x] 1.2 Add the `gpt-6-astra*` alias so dated snapshots resolve to the canonical entry.

## 2. Regression coverage

- [x] 2.1 Cover the standard, Fast, priority, and Flex tiers.
- [x] 2.2 Cover the standard and Flex long-context rates.
- [x] 2.3 Cover the 272,000-token boundary on both sides.
- [x] 2.4 Cover snapshot alias resolution.

## 3. Verification

- [x] 3.1 Run `tests/unit/test_pricing.py`.
- [x] 3.2 Run `ruff` and `ty`.
- [x] 3.3 Validate the scoped OpenSpec change with strict validation.

## 4. Out of scope

- [ ] 4.1 Per-surface rate cards. Codex charges Fast at 2.5x rather than 2x for this model, applies no long-context multiplier, and does not bill cache writes. The bundled table is uniformly the API rate card, so encoding surface-specific rates is a separate contract change.
- [ ] 4.2 `_GPT5_ALIAS_BASE_MODELS` in `app/modules/proxy/request_policy.py` is scoped to GPT-5 slugs for stripping Cursor-style UI suffixes; whether GPT-6 needs the same treatment is a routing question, not a pricing one.
