## MODIFIED Requirements

### Requirement: T3 settings have a database home

Every `Settings` field declared T3 MUST have a `dashboard_settings` column of the same name, or a database home declared in the `DASHBOARD_HOMES` registry in `app/core/config/tiers.py`, or MUST be listed in the `MIGRATING` registry in the same module. A `DASHBOARD_HOMES` entry maps the field name to the existing column that holds its value when the column's name differs from the field's or lives in another database configuration table, written `table.column`; the CI check MUST verify that the column exists and MUST fail otherwise, so a home cannot be declared ahead of its column or survive the column's removal. A `MIGRATING` entry maps the field name to its target dashboard home — the column, table or existing setting the field folds into — or to the literal `"backlog"` when no home has been designed yet; an entry with any other value or an empty value is invalid. `MIGRATING` is the backlog of the environment-to-dashboard migration: the change that gives the field its column MUST delete the entry in the same diff (adding a `DASHBOARD_HOMES` entry when the column is not named after the field), and the CI check reports an entry that is redundant (the same-name column exists, `DASHBOARD_HOMES` maps the field, the field is not T3, or the field no longer exists) as a warning until it is deleted. A PR MUST NOT add a new T3 field that lives only in the environment: a new T3 field ships with its dashboard column, and a `MIGRATING` entry for a new field is accepted only when the PR body names the follow-up change that adds the column. The initial `MIGRATING` content is the set of T3 fields that were environment-only when the registry was created; it only shrinks thereafter, and an EMPTY `MIGRATING` is its terminal state rather than a check failure: it means every T3 field has a database home. The CI check MUST pass on an empty registry and MUST still fail a new T3 field that has neither a same-name column, a `DASHBOARD_HOMES` mapping nor an entry.

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

#### Scenario: Backlog reaches empty

- **GIVEN** the last `MIGRATING` entry is deleted because its field gained a
  dashboard home or became a fixed constant
- **WHEN** `make lint` runs the tier check
- **THEN** the empty registry passes with no error and no warning
- **AND** a T3 field added afterwards with no dashboard home still fails the
  check
