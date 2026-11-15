## Verification — 2026-11-15

- Focused current-code run: `python -m pytest tests/unit/test_api_keys_repository.py tests/unit/test_api_key_limit_reset_scheduler.py tests/integration/test_api_keys_api.py -q --show-capture=no` — **118 passed**, one existing Starlette/AnyIO deprecation warning, 97.75 seconds.
- Regression reproduction restores the old method in a separate Python process only (repository files remain fixed): the cursor unit regression and all four SQLite integration cases fail (**5 failed, 111 deselected**). Direct sweep cases stop at the deterministic SELECT bound; scheduler cases prove maintenance cannot proceed to stale-reservation release before the fix.
- SQLite cases use real database storage, legacy second-precision text, batch sizes one and two, an entire skipped batch, later resettable rows and a future row. Both repository and scheduler entry paths pass after the fix.
- Focused Ruff check and formatting check: all three changed Python files pass.
- `openspec validate bound-expired-api-key-limit-sweep --strict --no-interactive`: valid.
- `openspec validate --specs --strict --no-interactive`: **67 passed, 0 failed**.
- `git diff --check` for the affected files: clean.

This verifies termination and later-row progress while preserving exact timestamp ownership guards. Legacy timestamp rows remain unchanged; normalization and lazy-reset/reservation representation compatibility are outside this fix. PostgreSQL and the complete cross-stack CI matrix were not run for this focused change.
