## Why

The configuration policy (dashboard first, env only for bootstrap and instance topology; `configuration-tiers` capability, introduced by the `codify-configuration-tiers` change) is only as good as its enforcement. The 2026-09 configuration inventory found 76 behaviour tunables and flags living only in env, 14 direct `os.environ` reads outside `Settings`, and a settings surface that regrew from 114 to 135 fields after the #1340 reduction — the "deferred" field-count ratchet was never added. Reviewer memory does not hold these lines; CI must.

## What Changes

- Add `app/core/config/tiers.py`: a `SETTING_TIERS` map assigning every `Settings` field a tier (`T0`–`T4`) and a `MIGRATING` map for T3 fields that still have no `dashboard_settings` home (initially every env-only tunable/flag from the inventory, value `backlog`).
- Add `scripts/check_settings_tiers.py`, run by `make lint` (architecture-check): fails on fields without a tier, on T3 fields with neither a same-name `dashboard_settings` column nor a `MIGRATING` entry, on `os.environ` / `os.getenv` / `dotenv_values` use under `app/` outside `app/core/config/settings.py` (an explicit allowlist covers the current sites until they are promoted to `Settings` fields), on `.env.example` mentioning a T2/T3/T4 setting, and on `len(Settings.model_fields)` exceeding the new `[settings_fields]` budget. Entries for fields that no longer exist and allowlist entries that no longer match only warn, so removals and this map can land in either order.
- Add `[settings_fields] max = 135` to `.github/simplicity-budgets.toml`; `tests/unit/test_settings_reference.py` reads its ratchet from the same key instead of a duplicated constant.
- `scripts/generate_settings_reference.py` renders a **Tier** column and a tier legend in `docs/reference/settings.md`.

## Capabilities

### New Capabilities

None. (`configuration-tiers` is introduced by the sibling `codify-configuration-tiers` change; this change only adds the CI-enforcement requirements to it.)

### Modified Capabilities

- `configuration-tiers`: ADDED requirements for the tier map, the mechanical checks, the settings-field ratchet, and the tier column in the generated reference.

## Impact

- `app/core/config/tiers.py` (new), `scripts/check_settings_tiers.py` (new), `Makefile` (`architecture-check`), `.github/simplicity-budgets.toml`, `scripts/generate_settings_reference.py`, `docs/reference/settings.md` (regenerated), `tests/unit/test_settings_tiers.py` (new), `tests/unit/test_settings_reference.py`.
- No runtime behaviour change; no new setting; no schema change. The CI lint job now fails PRs that add an untiered or env-only T3 setting, a direct environment read, or a 136th field.
