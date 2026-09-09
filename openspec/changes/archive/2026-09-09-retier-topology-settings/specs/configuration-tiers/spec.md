## MODIFIED Requirements

### Requirement: Every setting declares a tier

`app/core/config/tiers.py` SHALL map every `Settings` field name to exactly one tier in `T0` (bootstrap), `T1` (instance topology), `T2` (secret), `T3` (behaviour tunable or feature flag), `T4` (incident debug). A T3 field that has no `dashboard_settings` column of the same name MUST either be mapped in `DASHBOARD_HOMES` to its existing database home as `table.column` or be listed in `MIGRATING` with its target dashboard home or `backlog`. `scripts/check_settings_tiers.py`, run by `make lint`, SHALL fail when a field has no tier, when a tier value is unknown, when a T3 field has none of a same-name `dashboard_settings` column, a `DASHBOARD_HOMES` mapping or a `MIGRATING` entry, or when a `DASHBOARD_HOMES` target is not of the form `table.column` or names a column that does not exist in the database metadata. A tier, `DASHBOARD_HOMES` or `MIGRATING` entry for a field that no longer exists, a `MIGRATING` or `DASHBOARD_HOMES` entry whose field already has a same-name dashboard column or is no longer T3, or a `MIGRATING` entry for a field that `DASHBOARD_HOMES` already maps, SHALL be reported as a warning and SHALL NOT fail the check; a redundant `DASHBOARD_HOMES` entry is classified before its target is validated, so its target may be malformed or name a dropped column without failing the check.

#### Scenario: New setting without a tier

- **WHEN** a PR adds a `Settings` field and does not add it to `SETTING_TIERS`
- **THEN** `make lint` fails naming the field

#### Scenario: New env-only behaviour tunable

- **WHEN** a PR adds a `Settings` field tiered `T3` that has no same-name `dashboard_settings` column, no `DASHBOARD_HOMES` mapping and no `MIGRATING` entry
- **THEN** `make lint` fails naming the field and the ways to resolve it

#### Scenario: Setting removed before its tier entry

- **WHEN** a PR removes a `Settings` field but `SETTING_TIERS`, `DASHBOARD_HOMES` or `MIGRATING` still lists it
- **THEN** the check passes with a warning naming the stale entry, even when the `DASHBOARD_HOMES` target column was dropped in the same change; the same holds for a `DASHBOARD_HOMES` entry made redundant by a same-name column or a re-tier

#### Scenario: Declared home must exist

- **WHEN** `DASHBOARD_HOMES` maps a field to a target that is not `table.column`, or to a column that no database table defines
- **THEN** `make lint` fails naming the field and the target

### Requirement: Precedence is code default, then environment, then dashboard

For every T3 setting the effective value MUST be resolved as: the dashboard value when it is non-NULL; otherwise the environment value when the setting has an environment fallback and the variable is set; otherwise the code default. An environment value MUST NOT override a non-NULL dashboard value, and no code path MAY invert this order (environment-wins kill switches, environment values that gate whether a dashboard value is honoured, sentinel dashboard values that defer to the environment, or `max()`/`min()` merges of environment and dashboard values are all prohibited). A field whose dashboard home is declared in `DASHBOARD_HOMES` follows the same order: the persisted value in the target column wins, the environment variable applies only while that column holds no decision (the `telemetry` specification mandates exactly this for `CODEX_LB_TELEMETRY_ENABLED` and `dashboard_settings.telemetry_consent`). Where another capability specification currently mandates an inversion (the `rate-limit-reset-credits` polling toggle that gates `auto_redeem_reset_credits_before_expiry`), that specification MUST be amended to this precedence in the same change that removes the inversion from code; until then the inversion is a tracked defect, not an exception to this requirement.

#### Scenario: Dashboard value wins over environment

- **GIVEN** a T3 setting with a non-NULL dashboard value and a different environment value
- **WHEN** the effective value is resolved
- **THEN** the dashboard value is used

#### Scenario: Environment fills a NULL dashboard value

- **GIVEN** a T3 setting whose dashboard value is NULL and whose environment variable is set
- **WHEN** the effective value is resolved
- **THEN** the environment value is used

#### Scenario: Code default applies when neither is set

- **GIVEN** a T3 setting whose dashboard value is NULL and whose environment variable is unset
- **WHEN** the effective value is resolved
- **THEN** the code default is used

#### Scenario: Environment fallback ends when a decision is persisted

- **GIVEN** `CODEX_LB_TELEMETRY_ENABLED=false` and no telemetry decision saved in the dashboard
- **WHEN** the operator enables telemetry in the dashboard
- **THEN** the persisted decision is the effective value and the environment variable no longer applies while it stays set

### Requirement: T3 settings have a database home

Every `Settings` field declared T3 MUST have a `dashboard_settings` column of the same name, or a database home declared in the `DASHBOARD_HOMES` registry in `app/core/config/tiers.py`, or MUST be listed in the `MIGRATING` registry in the same module. A `DASHBOARD_HOMES` entry maps the field name to the existing column that holds its value when the column's name differs from the field's or lives in another database configuration table, written `table.column`; the CI check MUST verify that the column exists and MUST fail otherwise, so a home cannot be declared ahead of its column or survive the column's removal. A `MIGRATING` entry maps the field name to its target dashboard home — the column, table or existing setting the field folds into — or to the literal `"backlog"` when no home has been designed yet; an entry with any other value or an empty value is invalid. `MIGRATING` is the backlog of the environment-to-dashboard migration: the change that gives the field its column MUST delete the entry in the same diff (adding a `DASHBOARD_HOMES` entry when the column is not named after the field), and the CI check reports an entry that is redundant (the same-name column exists, `DASHBOARD_HOMES` maps the field, the field is not T3, or the field no longer exists) as a warning until it is deleted. A PR MUST NOT add a new T3 field that lives only in the environment: a new T3 field ships with its dashboard column, and a `MIGRATING` entry for a new field is accepted only when the PR body names the follow-up change that adds the column. The initial `MIGRATING` content is the set of T3 fields that were environment-only when the registry was created; it only shrinks thereafter.

#### Scenario: New environment-only tunable is rejected

- **WHEN** a PR adds a `Settings` field declared T3 with no `dashboard_settings` column of the same name, no `DASHBOARD_HOMES` mapping and no `MIGRATING` entry
- **THEN** `make lint` fails and the PR is not merged until the column exists or the field is re-tiered

#### Scenario: Migration entry names its target

- **GIVEN** a T3 field that is still environment-only
- **WHEN** it is listed in `MIGRATING`
- **THEN** the entry's value is the target dashboard column, table or setting it folds into (for example `model_context_window_overrides → backlog`, or `<field> → fold into <existing dashboard setting>` when the field folds into a setting that already has a column), or `"backlog"` when none has been designed

#### Scenario: Column lands and the entry is deleted

- **GIVEN** a T3 field listed in `MIGRATING`
- **WHEN** the change that adds its `dashboard_settings` column is merged
- **THEN** the same diff deletes the `MIGRATING` entry; if it does not, `make lint` warns that the entry is redundant until a follow-up deletes it

#### Scenario: Home under a different column name is declared explicitly

- **GIVEN** the T3 field `telemetry_enabled`, whose persisted value is `dashboard_settings.telemetry_consent`
- **WHEN** the registry is checked
- **THEN** `DASHBOARD_HOMES` maps `telemetry_enabled` to `dashboard_settings.telemetry_consent`, the field is not listed in `MIGRATING`, and `make lint` passes; a mapping to a column that does not exist fails the check
