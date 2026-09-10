## ADDED Requirements

### Requirement: Every setting declares a tier

`app/core/config/tiers.py` SHALL map every `Settings` field name to exactly one tier in `T0` (bootstrap), `T1` (instance topology), `T2` (secret), `T3` (behaviour tunable or feature flag), `T4` (incident debug). A T3 field that has no `dashboard_settings` column of the same name MUST be listed in `MIGRATING` with its target dashboard home or `backlog`. `scripts/check_settings_tiers.py`, run by `make lint`, SHALL fail when a field has no tier, when a tier value is unknown, or when a T3 field has neither a same-name `dashboard_settings` column nor a `MIGRATING` entry. A tier or `MIGRATING` entry for a field that no longer exists, or a `MIGRATING` entry whose field already has a dashboard column, SHALL be reported as a warning and SHALL NOT fail the check.

#### Scenario: New setting without a tier

- **WHEN** a PR adds a `Settings` field and does not add it to `SETTING_TIERS`
- **THEN** `make lint` fails naming the field

#### Scenario: New env-only behaviour tunable

- **WHEN** a PR adds a `Settings` field tiered `T3` that has no same-name `dashboard_settings` column and no `MIGRATING` entry
- **THEN** `make lint` fails naming the field and the two ways to resolve it

#### Scenario: Setting removed before its tier entry

- **WHEN** a PR removes a `Settings` field but `SETTING_TIERS` or `MIGRATING` still lists it
- **THEN** the check passes with a warning naming the stale entry

### Requirement: Direct environment reads are confined to the settings module

Under `app/`, `os.environ`, `os.getenv`, and `dotenv_values` SHALL be referenced only in `app/core/config/settings.py`. `scripts/check_settings_tiers.py` SHALL fail on any other reference, except in files named in its explicit allowlist of pre-existing sites (each entry recording the variable it reads and the number of lines that read the environment today). It SHALL fail when an allowlisted file exceeds its recorded number of reading lines and SHALL warn when an allowlisted file has fewer reads than recorded or no longer reads the environment.

#### Scenario: New direct environment read

- **WHEN** a PR adds `os.getenv("X")` to a module under `app/` that is not `app/core/config/settings.py` and not allowlisted
- **THEN** `make lint` fails with the file and line, pointing to a `Settings` field as the replacement

#### Scenario: New read added to an allowlisted file

- **WHEN** a PR adds a second `os.getenv("Y")` to an allowlisted file whose entry records one reading line
- **THEN** `make lint` fails naming the file, its reading lines, and the recorded cap

#### Scenario: Allowlisted site is migrated

- **WHEN** a PR removes the last direct environment read from an allowlisted file without dropping the allowlist entry
- **THEN** the check passes with a warning naming the stale entry

### Requirement: The operator env template lists only bootstrap and topology settings

`.env.example` SHALL mention only settings tiered `T0` or `T1` (`PORT` and non-setting text are unaffected). `scripts/check_settings_tiers.py` SHALL fail when a `CODEX_LB_*` variable in `.env.example`, commented or not, resolves to a T2, T3, or T4 field.

#### Scenario: Behaviour tunable added to .env.example

- **WHEN** a PR adds `# CODEX_LB_<T3_FIELD>=...` to `.env.example`
- **THEN** `make lint` fails naming the variable and its tier

### Requirement: The settings surface is ratcheted

`.github/simplicity-budgets.toml` SHALL carry a `[settings_fields]` section whose `max` is the allowed `len(Settings.model_fields)`. `scripts/check_settings_tiers.py` SHALL fail when the field count exceeds `max` and exit with a configuration error when the section is missing; `tests/unit/test_settings_reference.py` SHALL read the same value. Removing a field SHOULD lower `max` in the same diff; raising it requires the `simplicity-budget-approved` label and a why-not-a-default justification in the PR body.

#### Scenario: Field added over budget

- **WHEN** a PR adds a `Settings` field and `len(Settings.model_fields)` exceeds `[settings_fields].max`
- **THEN** `make lint` fails stating the count and the budget

#### Scenario: Budget section removed

- **WHEN** `[settings_fields]` is absent from `.github/simplicity-budgets.toml`
- **THEN** the check exits with a configuration error instead of passing silently

### Requirement: The generated settings reference shows each setting's tier

`scripts/generate_settings_reference.py` SHALL render a **Tier** column from `SETTING_TIERS` in every table of `docs/reference/settings.md` and a legend describing the five tiers.

#### Scenario: Reference regenerated

- **WHEN** `uv run python scripts/generate_settings_reference.py` runs
- **THEN** every row of `docs/reference/settings.md` carries the setting's tier and the page contains the tier legend
