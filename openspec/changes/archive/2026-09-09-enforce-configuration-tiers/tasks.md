## 1. Tier declaration

- [x] 1.1 Add `app/core/config/tiers.py` with `SETTING_TIERS` covering every current `Settings` field (tier from the 2026-09 inventory category, corrected against the policy tier table) and `MIGRATING` for every T3 field without a same-name `dashboard_settings` column.

## 2. Mechanical checks

- [x] 2.1 Add `scripts/check_settings_tiers.py` with the coverage, T3-home, direct-env-read, `.env.example`, and field-count checks; stale tier/migrating/allowlist entries warn instead of failing.
- [x] 2.2 Add `[settings_fields]` to `.github/simplicity-budgets.toml` and make `tests/unit/test_settings_reference.py` read its ratchet from it.
- [x] 2.3 Wire the script into `make lint` (`architecture-check`), which the CI `lint` job and the `local-ci` pre-commit hook already run.

## 3. Documentation

- [x] 3.1 Render a Tier column and legend in `scripts/generate_settings_reference.py` and regenerate `docs/reference/settings.md`.

## 4. Verification

- [x] 4.1 Add `tests/unit/test_settings_tiers.py` (every field mapped, T3 without a column fails, env-read detection and allowlist, `.env.example` tiers, ratchet).
- [x] 4.2 Run `make lint`, the touched unit tests, the full unit slice, and `openspec validate --specs` / `openspec validate enforce-configuration-tiers`.
