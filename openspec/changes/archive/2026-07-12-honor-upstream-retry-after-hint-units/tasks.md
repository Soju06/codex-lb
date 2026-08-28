# Tasks

- [x] Extend `parse_retry_after` to honor minute/hour and compound `<num><unit>` retry hints
- [x] Keep the `None` fallback for unparseable hints so `handle_rate_limit` backoff is unchanged
- [x] Add regression coverage for minute, hour, compound, and word-form hints in `tests/unit/test_retry.py`
- [x] Require a non-letter boundary after each unit so an unsupported longer word (`month` -> `m`) is not mis-read as a shorter unit
- [x] Add product-path regression coverage in `tests/unit/test_load_balancer.py` proving `handle_rate_limit` sets the cooldown from a word-unit hint and falls back to backoff for an unsupported longer word
- [x] Document the account-routing cooldown requirement delta (proposal + ADDED requirement with GIVEN/WHEN/THEN scenarios, including the unit-boundary scenario)
- [x] Run `uv run --frozen ruff check .` and `uv run --frozen ruff format --check .`
- [x] Run `uv run --frozen pytest tests/unit/test_retry.py`
