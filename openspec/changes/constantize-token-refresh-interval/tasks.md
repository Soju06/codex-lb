- [x] `fast_canary_suite.py`: add `stamp_isolated_auth_refresh` and call it after
  `validate_config`, before either runner; delete the
  `CODEX_LB_TOKEN_REFRESH_INTERVAL_DAYS=365` failure-environment injection.
- [x] `app/core/auth/refresh.py`: `TOKEN_REFRESH_INTERVAL_DAYS: Final[int] = 8`;
  `should_refresh` reads the module attribute instead of `get_settings()`; drop
  the now-unused `get_settings` import.
- [x] Delete `token_refresh_interval_days` from `Settings`; add
  `CODEX_LB_TOKEN_REFRESH_INTERVAL_DAYS` to `_REMOVED_SETTINGS`; drop the
  `SETTING_TIERS` and `MIGRATING` rows, leaving `MIGRATING` empty.
- [x] Confirm `scripts/check_settings_tiers.py` passes with an empty `MIGRATING`
  and document that terminal state in its docstring and in `tiers.py`.
- [x] Regenerate `docs/reference/settings.md`; lower `[settings_fields].max` to
  95; add the removed env name to the Helm chart README's 1.24 -> 1.25 register.
- [x] `docs/traffic-parity.md`: document the credential stamp and the retired
  pin. `docs/configuration.md`: stop describing the backlog as non-empty.
- [x] Tests: canary suite asserts the stamp keeps `should_refresh` false and that
  no `CODEX_LB_*` refresh pin is exported; `test_auth_refresh` monkeypatches the
  constant; `_REMOVED_SETTINGS` counts extended; `test_settings_tiers` pins the
  empty backlog.
- [x] Spec deltas: `deployment-installation`, `compatibility-tooling`,
  `configuration-tiers`.
- [x] Verification: `make lint`, `uv run ty check`, `make migration-check`, the
  touched test files in full, `uv run pytest tests/unit`, the auth/settings
  integration files, `python3 .github/scripts/check_simplicity_budgets.py`,
  `openspec validate --specs --strict`, `openspec validate
  constantize-token-refresh-interval --strict`, and an archive simulation
  against a copy of main's `openspec/`.
