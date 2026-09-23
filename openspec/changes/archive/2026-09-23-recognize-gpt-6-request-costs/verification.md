# Verification

- Reproduced the missing-price failure with the new GPT-6 alias regression before adding pricing.
- `uv run pytest tests/unit/test_pricing.py tests/unit/test_usage.py tests/unit/test_api_keys_service.py tests/unit/test_proxy_api_key_usage.py -q`: 269 passed.
- `uv run pytest tests/integration/test_api_keys_api.py tests/integration/test_request_logs_api.py -q`: 121 passed.
- Additional API assertions verify the persisted tier and public `costUsd`. The existing proxy canonicalizes `fast` to `priority`; the regression asserts that normalization and the same Fast cost. The targeted cases pass after correcting that test expectation.
- Ruff check and format check passed for all four changed Python files; `git diff --check` passed.
- Independent code review found no actionable issues. Existing explicit priority overrides retain their behavior; prior multiplier-priced models have no long-context rate fields.
- Strict validation passes for this change. Repository-wide `validate --specs --strict` reports 50 passing specs and 15 failing specs with pre-existing normative keyword violations. All 15 failing files were compared byte-for-byte with `HEAD` and are unchanged by this task; the owning `api-keys` spec passes.

Stable pricing context is synced to `openspec/specs/api-keys/context.md`. No deployment, persisted historical-cost rewrite, or API-key counter backfill was performed.
