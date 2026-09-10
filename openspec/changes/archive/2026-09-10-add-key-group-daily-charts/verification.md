## Verification

- `.venv/bin/pytest -q tests/integration/test_key_dashboard_groups.py` — 11 passed.
- Frontend integration and locale tests — 17 passed.
- Frontend ESLint for the key-dashboard feature and integration test — passed.
- TypeScript project check and Vite production build — passed.
- `.venv/bin/ruff check app/modules/key_dashboard tests/integration/test_key_dashboard_groups.py` — passed.
- Browser preview at desktop and 390px mobile widths — chart lines, Tokens/Cost controls, member visibility, daily table, and no page-level overflow verified. Screenshots: `docs/screenshots/key-group-daily-tokens.png`, `docs/screenshots/key-group-daily-cost.png`, and `docs/screenshots/key-group-daily-mobile.png`.
- Strict OpenSpec validation and main-spec synchronization completed before archive.
