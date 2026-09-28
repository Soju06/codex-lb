# Tasks

## 1. Compatibility metadata

- [x] 1.1 Add 128,000-token fallbacks for `gpt-6-astra`, `gpt-6-sol`, and `gpt-6-luna` to `_V1_MAX_OUTPUT_TOKEN_OVERRIDES`.
- [x] 1.2 Preserve explicit integer `max_output_tokens` from raw upstream model metadata as higher priority than the slug fallback.
- [x] 1.3 Add `/v1/models` regression coverage for all three GPT-6 slugs and raw-upstream precedence.

## 2. Verification

- [x] 2.1 Run the targeted `/v1/models` integration tests.
- [x] 2.2 Run spec validation and diff checks.
