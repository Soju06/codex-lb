# Verification

Based on upstream main `f8ffbac2`.

- 211 backend tests passed across the settings/migration, account-import, focused OAuth and relevant unit suites (100 + 25 + 12 + 74).
- Migration upgrade/downgrade/upgrade preserves mixed account preferences and the global warm-up flag, retains a saved enrollment choice on re-upgrade, and reports no schema drift.
- 66 frontend tests passed for Routing settings, settings hooks and payload construction.
- `make lint` passed, including migration topology and configuration-tier checks; `uv run ty check` passed.
- Frontend ESLint, TypeScript and production build passed.
- Strict validation passed for this OpenSpec change and all main specifications.
- Synthetic desktop/mobile before-and-after captures passed; the new switch defaults off.

## Requirement coverage

- New-account enrollment and global-switch independence: account-import API matrix, device OAuth and browser callback tests.
- Preserve account preferences: reimport and reauthentication cases with both stored values and both enrollment defaults.
- Settings persistence and omission: GET/PUT round trip and unrelated-update checks.
- Migration hygiene: current upstream single head, both upgrade directions, mixed existing rows, idempotent re-upgrade and schema drift.
- UI controls: global warm-up off, persisted choice after refetch, busy state, existing authorization gates.

## Commands

```sh
make lint
uv run ty check
uv run pytest tests/integration/test_accounts_api.py tests/integration/test_settings_api.py tests/integration/test_new_account_warmup_migration.py -q --timeout=120 --timeout-method=thread
uv run pytest tests/integration/test_oauth_flow.py -k 'device_oauth_flow_creates_account or device_oauth_reauth_reuses_existing_row_for_same_chatgpt_identity or manual_callback_creates_account_or_preserves_warmup_on_reauth' -q --timeout=120 --timeout-method=thread
uv run pytest tests/unit/test_settings_service.py tests/unit/test_settings_cache.py tests/unit/test_accounts_service_transitions.py tests/unit/test_settings_repository_seed.py tests/unit/test_settings_multi_replica.py -q
cd frontend
node node_modules/vitest/vitest.mjs run src/features/settings/components/routing-settings.test.tsx src/features/settings/hooks/use-settings.test.ts src/features/settings/payload.test.ts
node node_modules/eslint/bin/eslint.js src
node node_modules/@typescript/native/bin/tsc -b
node node_modules/vite/bin/vite.js build
```

Strict OpenSpec validation used CLI 1.12.0. PostgreSQL and the complete cloud CI matrix remain upstream merge gates. An early OAuth run exceeded its one-second polling window during concurrent builds; the isolated 12-case OAuth suite subsequently passed. No production database or deployment was used. Keep the change active through upstream review.
