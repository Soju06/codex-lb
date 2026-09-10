# configuration-tiers Specification

## Purpose
Defines where every codex-lb setting lives and how its value is resolved. Each setting belongs to exactly one tier — T0 bootstrap, T1 instance topology, T2 secret, T3 behaviour tunable, T4 incident debug — chosen by whether the value may legitimately differ between two replicas. The dashboard is the primary configuration surface: T3 values resolve as code default, then environment, then a non-NULL dashboard value; the environment is a fallback, never a seed copied into the dashboard row and never an override of a persisted dashboard value. This capability also fixes the single resolver and snapshot-consumer rule, the settings API provenance shape, the tier registry (`SETTING_TIERS` / `MIGRATING`), the settings-field budget, confinement of `os.environ` reads to the settings module, T0/T1-only operator documentation, and the one-release retirement path for environment names, together with the `make lint` checks that enforce them.
## Requirements
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

### Requirement: Every setting is assigned a configuration tier

Every configurable value SHALL belong to exactly one tier, chosen with the discriminating question "may the value legitimately differ between two replicas of the same deployment?":

| Tier | Name | Store | Restart to change | Differs between replicas | Examples |
|------|------|-------|-------------------|--------------------------|----------|
| T0 | Bootstrap | environment only | yes | no (must be identical) | data directory, database URL, encryption key file, listen port, migration policy, dashboard bootstrap token |
| T1 | Instance topology | environment only | yes | yes (legitimately) | bridge instance id / ring / advertise URL, OAuth callback host, trusted-proxy CIDRs and headers, leader election on/off, worker and pool sizes |
| T2 | Secret | encrypted database column (dashboard) with an optional environment seed | no | no | upstream proxy credentials, telemetry tokens |
| T3 | Behaviour tunable | `dashboard_settings` (or another database configuration table) | no | no | routing strategy, caps, timeouts, retries, circuit breakers, retention, feature toggles, image and model policy |
| T4 | Incident debug | environment permitted; dashboard toggle recommended | no | yes | trace channels |

The tier is decided in order: a value that is needed before the database is reachable is T0; otherwise a value that may legitimately differ between two replicas is T1 (or T4 for a debug-only channel); otherwise a credential or token is T2; every other operator-changeable value is T3. The tier of every field of `Settings` in `app/core/config/settings.py` MUST be declared in the tier registry `SETTING_TIERS` in `app/core/config/tiers.py` (field name → `"T0"` | `"T1"` | `"T2"` | `"T3"` | `"T4"`), which is the single source of the tier for the CI check (`scripts/check_settings_tiers.py`, run by `make lint`), the generated settings reference and reviewers; the tier MUST NOT be encoded in field metadata or elsewhere. A PR that adds a `Settings` field MUST add its registry entry in the same diff; a PR that removes a field SHOULD drop the entry in the same diff (a stale entry is tolerated as a warning so removals can land in either order). The number of `Settings` fields is budgeted by `[settings_fields].max` in `.github/simplicity-budgets.toml`: a PR that removes a field SHOULD lower `max` in the same diff, and a PR that adds a field MUST raise `max` in the same diff together with the P2 "why not a default" justification in its body — `max` is never raised ahead of the field it admits.

#### Scenario: Replica question places a per-instance value in the environment

- **GIVEN** a new setting whose correct value differs between two replicas (for example an advertise URL)
- **WHEN** the setting is added to `Settings`
- **THEN** it is declared T1 in `SETTING_TIERS` and lives only in the environment

#### Scenario: Replica question places a shared runtime knob in the dashboard

- **GIVEN** a new setting that must hold the same value on every replica and does not need to exist before the database is reachable (for example a stream idle timeout)
- **WHEN** the setting is added
- **THEN** it is declared T3 in `SETTING_TIERS` and is stored in `dashboard_settings` (or another database configuration table)

#### Scenario: Registry entry travels with the field

- **GIVEN** a PR that adds a `Settings` field
- **WHEN** the PR is reviewed
- **THEN** the same diff adds the field's `SETTING_TIERS` entry and raises `[settings_fields].max` by one with the P2 justification; a diff that raises `max` without adding a field, or adds a field without its registry entry, is rejected

#### Scenario: Field removal lowers the budget

- **GIVEN** a PR that deletes a `Settings` field
- **WHEN** the PR is reviewed
- **THEN** the same diff drops the field's `SETTING_TIERS` (and, if present, `MIGRATING`) entry and lowers `[settings_fields].max` by one; if the entries are dropped in a later PR instead, the interim state is a warning, not a failure

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

### Requirement: Environment values are fallbacks, never seeds

When the settings row is created for the first time, every T3 dashboard column that has an environment fallback MUST be left NULL. The creation path MUST NOT copy process environment values into non-NULL dashboard columns. A NULL dashboard column continues to inherit the environment value (or code default) until an operator explicitly sets a value through the dashboard or the settings API. A NOT NULL dashboard column that is seeded once from an environment field which no other code reads MUST be resolved by deleting the environment field (the column's code default becomes the only default), not by making the column nullable; the deleted name follows the retirement rule below. Rows that already exist when the seed is removed MUST be left as they are: a non-NULL value whose provenance is unknown (it may be a seed or an operator edit) MUST NOT be cleared by a migration; the operator clears it through the dashboard or the settings API.

#### Scenario: Environment change after first boot takes effect

- **GIVEN** a fresh install whose settings row was created while `CODEX_LB_PROXY_ACCOUNT_STREAM_LIMIT=8` was set and no operator has edited the cap
- **WHEN** the operator restarts with `CODEX_LB_PROXY_ACCOUNT_STREAM_LIMIT=12`
- **THEN** the effective stream cap is 12 and the settings API reports `source: "env"`

#### Scenario: Operator edit stops the inheritance

- **GIVEN** a T3 setting inheriting its environment value
- **WHEN** an operator sets a value through `PUT /api/settings`
- **THEN** the dashboard column becomes non-NULL, the effective value is the operator's value, and later environment changes have no effect until the operator clears the value

#### Scenario: Existing rows are not cleared when the seed is removed

- **GIVEN** an existing install whose cap columns hold non-NULL values written by the first-boot seed or by an operator
- **WHEN** the release that removes the seed is applied
- **THEN** no migration sets those columns to NULL; the settings API reports `source: "dashboard"` for them until the operator clears the value

#### Scenario: Seed-once column loses its environment field

- **GIVEN** a NOT NULL `dashboard_settings` column whose only environment reader is the first-row seed (for example `warmup_model`)
- **WHEN** the column is aligned to this requirement
- **THEN** the environment field is removed from `Settings`, its name is added to the removed-settings registry, and first-row creation persists the column's code default

### Requirement: One resolver computes effective values and consumers read the snapshot

The effective value of a T3 setting MUST be computed only by the effective-value functions of `SettingsService` in `app/modules/settings/service.py` (`_effective_<name>()`); call sites MUST NOT re-implement the combination. Code that consumes a T3 setting MUST read it from the `SettingsCache` snapshot and MUST NOT read `get_settings().<field>` directly. Cross-replica propagation of dashboard edits SHALL use the existing `settings` cache-invalidation namespace; no restart or leader election is involved.

#### Scenario: Direct environment read of a T3 field fails lint

- **WHEN** code outside the `SettingsService` effective-value functions reads `get_settings().<field>` for a field declared T3
- **THEN** the architecture check in `make lint` fails

#### Scenario: Edit on one replica is observed by another

- **GIVEN** two replicas sharing one database
- **WHEN** an operator changes a T3 setting through the dashboard on replica A
- **THEN** replica B uses the new effective value after its settings cache is invalidated, without a restart

### Requirement: The settings API reports value, source, environment value and default

For every inheritable setting — a setting whose NULL dashboard column falls back to the environment or to the code default — `GET /api/settings` and the response of `PUT /api/settings` MUST report the effective value together with its provenance, so an operator can tell whether a number is the code default, an environment variable set on this deployment, or a value saved in the dashboard. The shape is additive: the effective value stays in the existing top-level field of the setting's name (`value` is not duplicated), and `provenance`, a map keyed by setting name (the `dashboard_settings` column name, which is also the `Settings` field name for settings with an environment fallback), carries one entry per inheritable setting with `source` (`"dashboard"`, `"env"` or `"default"`), `env_value` and `default`. `source` MUST be computed by the single module-level resolver `resolve_inheritable` in `app/modules/settings/service.py` (no call site re-implements the precedence) as: `"dashboard"` when the column is non-NULL (including an explicit `0` or `false`); otherwise `"env"` when the setting has an environment fallback and the environment value differs from the code default; otherwise `"default"`. `env_value` MUST be the process environment value (the code default when the variable is unset) for a setting with an environment fallback and `null` for a database-only setting. `env_value` and `default` carry the setting's own scalar type. Adding `provenance` MUST NOT change any pre-existing response field: the flat `<name>`, `<name>_environment_value` and `<name>_override` fields remain as they are, a client that does not know `provenance` MUST keep working, and the dashboard MUST keep working against a backend that omits it. A setting promoted from the environment to the dashboard MUST be resolved through the same resolver and appear in `provenance`.

`PUT /api/settings` MUST treat every inheritable setting as tri-state: a field that is omitted leaves the dashboard value unchanged, an explicit `null` clears the dashboard column so the setting returns to inheritance (`source` becomes `"env"` or `"default"`), and a concrete value is stored and wins over both. For every inheritable setting the dashboard exposes — the four account-capacity caps, the two retention windows, and every setting promoted from the environment to the dashboard — the dashboard MUST use the shared inherited affordance (`InheritBadge` / `useInheritableSetting`): a setting whose `source` is not `"dashboard"` is rendered as inherited, naming the layer and the value it inherits ("inherited from environment (12)", "default (8)"), and a setting whose `source` is `"dashboard"` offers a "reset to inherited" action that sends the existing tri-state `PUT /api/settings` with an explicit `null` for that setting and nothing else changed. No settings form renders a bespoke effective-value hint in place of the shared affordance; a form MAY fall back to a plain hint only when the backend response carries no `provenance`.

#### Scenario: Provenance of an operator-set value

- **GIVEN** an operator has set `proxy_account_stream_limit` to 24 through `PUT /api/settings` while the code default is 8
- **WHEN** `GET /api/settings` is called
- **THEN** `proxyAccountStreamLimit` is 24 and `provenance.proxy_account_stream_limit` is `{"source": "dashboard", "envValue": 8, "default": 8}`

#### Scenario: Environment value differs from the default

- **GIVEN** `proxy_account_stream_limit` is NULL in `dashboard_settings` and `CODEX_LB_PROXY_ACCOUNT_STREAM_LIMIT=12` while the code default is 8
- **WHEN** `GET /api/settings` is called
- **THEN** `proxyAccountStreamLimit` is 12 and `provenance.proxy_account_stream_limit` is `{"source": "env", "envValue": 12, "default": 8}`

#### Scenario: Environment value equals the default

- **GIVEN** the same column is NULL and the variable is unset or set to 8
- **WHEN** `GET /api/settings` is called
- **THEN** `provenance.proxy_account_stream_limit` is `{"source": "default", "envValue": 8, "default": 8}`

#### Scenario: Clearing returns to inheritance

- **GIVEN** an inheritable setting with an environment fallback and a non-NULL dashboard value
- **WHEN** `PUT /api/settings` sets it to `null` and omits the other inheritable fields
- **THEN** that column becomes NULL, the omitted settings are unchanged, and the response reports `source` as `"env"` (variable set to a non-default value) or `"default"` for the cleared setting

#### Scenario: Resetting a database-only setting

- **GIVEN** `request_log_retention_days` has no environment fallback and its column is NULL
- **WHEN** `GET /api/settings` is called
- **THEN** `provenance.request_log_retention_days` is `{"source": "default", "envValue": null, "default": 0}`; once an operator stores `0` the entry reports `source: "dashboard"`, and an explicit `null` on `PUT` returns it to `source: "default"`

#### Scenario: Reset to inherited from the dashboard

- **GIVEN** the routing settings show a stream limit whose `source` is `"dashboard"`
- **WHEN** the operator activates "Reset to inherited"
- **THEN** the dashboard sends `PUT /api/settings` with `proxyAccountStreamLimit: null` and the other fields unchanged, and the refreshed response reports `source` `"env"` or `"default"` for the stream limit

#### Scenario: Retention windows use the shared inherited affordance

- **GIVEN** `request_log_retention_days` reports `source` `"default"` and `usage_history_retention_days` reports `source` `"dashboard"`
- **WHEN** the operator opens the Data retention card
- **THEN** the request-log window is labelled "default (0)" with an empty input and no bespoke "not configured" hint, and the usage-history window offers "Reset to inherited", which sends `PUT /api/settings` with `usageHistoryRetentionOverrideDays: null` and no other retention field

#### Scenario: Dashboard against an older backend

- **GIVEN** a backend response without `provenance`
- **WHEN** the dashboard parses it
- **THEN** parsing succeeds and the capacity inputs fall back to the effective-value hint derived from the flat `<name>EnvironmentValue` fields

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

### Requirement: Process environment is read only in the settings module

Under `app/`, `os.environ`, `os.getenv` and `dotenv_values` MUST be referenced only in `app/core/config/settings.py`. Every `CODEX_LB_*` variable the application consumes MUST be a `Settings` field with a declared tier, so that it appears in the generated settings reference and is covered by the removed-settings warning when retired. The only exception is the allowlist `ENV_READ_ALLOWLIST` in `scripts/check_settings_tiers.py`, which names, per file, the number of lines that read the environment and the variables they read; it exists for two kinds of read: pre-existing sites awaiting promotion to `Settings` fields (including the `CODEX_LB_*` names read outside `Settings` when the allowlist was created), and reads of variables the application does not define — third-party and POSIX conventions (`HTTP_PROXY`/`NO_PROXY`, `TZ`, `KUBERNETES_SERVICE_HOST`, `PROMETHEUS_MULTIPROC_DIR`, `GITHUB_TOKEN`) and the uvicorn launcher knobs (`HOST`, `PORT`, `SSL_*`, `UVICORN_*`) consumed before `Settings` is constructed. Each allowlist cap is a hard per-file ceiling that MAY only shrink: a PR MUST NOT add a file to the allowlist or raise a cap, and a PR that removes the last read from a file or promotes a variable to a `Settings` field MUST lower or delete the entry in the same diff (a cap left above the actual count is tolerated as a warning so the promotion and the allowlist edit can land in either order).

#### Scenario: Ad-hoc environment read fails lint

- **WHEN** a module under `app/` other than `app/core/config/settings.py` that is not in `ENV_READ_ALLOWLIST` references `os.environ`, `os.getenv` or `dotenv_values`
- **THEN** `make lint` fails and the variable is added as a `Settings` field with a tier instead

#### Scenario: Allowlist only shrinks

- **GIVEN** a file in `ENV_READ_ALLOWLIST` with a cap of one reading line
- **WHEN** a PR adds a second environment read to that file, adds a new file to the allowlist, or raises the cap
- **THEN** the PR is rejected; the new value is read through a `Settings` field

#### Scenario: Promoted variable appears in the reference

- **GIVEN** a `CODEX_LB_*` variable previously read through an allowlisted site
- **WHEN** it is promoted to a `Settings` field with a tier
- **THEN** the same diff lowers or deletes the file's allowlist entry, and `scripts/generate_settings_reference.py` lists the variable with its tier

### Requirement: Operator-facing environment documentation lists T0 and T1 only

`.env.example` and `docs/configuration.md` MUST list only T0 and T1 settings; a `CODEX_LB_*` variable whose tier is T2, T3 or T4 MUST NOT appear in `.env.example`, commented or not. The generated `docs/reference/settings.md` SHALL list every `Settings` field with the tier taken from `SETTING_TIERS` and SHALL carry a legend of the five tiers, so the reference is the operator-facing rendering of the registry.

#### Scenario: Tunable added to the sample env file

- **WHEN** a PR adds a T3 variable to `.env.example`
- **THEN** `make lint` fails and the value is documented as a dashboard setting instead

#### Scenario: Reference shows the tier

- **WHEN** `scripts/generate_settings_reference.py` runs
- **THEN** every listed variable carries the tier recorded for it in `SETTING_TIERS` and the page contains the tier legend

### Requirement: Environment settings are retired through one release of warnings

When a T3 setting gains a database home, its environment field SHALL be removed from `Settings` and its name added to the removed-settings registry (`_REMOVED_SETTINGS`, surfaced by `warn_removed_settings` at startup) so that operators who still set the variable receive a startup WARN for one stable release; the registry entry is deleted in the following stable release. An environment field with zero readers under `app/` SHALL be deleted in the change that discovers it, without a deprecation release.

#### Scenario: Migrated variable warns for one release

- **GIVEN** a variable whose setting moved to the dashboard in stable release N
- **WHEN** an operator starts release N with the variable still set
- **THEN** startup logs one WARN naming the variable and the dashboard setting that replaces it, and the value is ignored

#### Scenario: Registry entry expires

- **GIVEN** a variable added to the removed-settings registry in stable release N
- **WHEN** stable release N+1 is cut
- **THEN** the registry entry is deleted and the variable is silently ignored

#### Scenario: Unread variable is deleted outright

- **GIVEN** a `Settings` field that no module under `app/` reads
- **WHEN** the field is discovered
- **THEN** it is deleted in that change without a deprecation release

