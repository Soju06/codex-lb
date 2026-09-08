## ADDED Requirements

### Requirement: Every setting is assigned a configuration tier

Every configurable value SHALL belong to exactly one tier, chosen with the discriminating question "may the value legitimately differ between two replicas of the same deployment?":

| Tier | Name | Store | Restart to change | Differs between replicas | Examples |
|------|------|-------|-------------------|--------------------------|----------|
| T0 | Bootstrap | environment only | yes | no (must be identical) | data directory, database URL, encryption key file, listen port, migration policy, dashboard bootstrap token |
| T1 | Instance topology | environment only | yes | yes (legitimately) | bridge instance id / ring / advertise URL, OAuth callback host, trusted-proxy CIDRs and headers, leader election on/off, worker and pool sizes |
| T2 | Secret | encrypted database column (dashboard) with an optional environment seed | no | no | upstream proxy credentials, telemetry tokens |
| T3 | Behaviour tunable | `dashboard_settings` (or another database configuration table) | no | no | routing strategy, caps, timeouts, retries, circuit breakers, retention, feature toggles, image and model policy |
| T4 | Incident debug | environment permitted; dashboard toggle recommended | no | yes | trace channels |

The tier is decided in order: a value that is needed before the database is reachable is T0; otherwise a value that may legitimately differ between two replicas is T1 (or T4 for a debug-only channel); otherwise a credential or token is T2; every other operator-changeable value is T3. Every field of `Settings` in `app/core/config/settings.py` MUST declare its tier in field metadata (`json_schema_extra={"tier": "T0" | "T1" | "T2" | "T3" | "T4"}`), and the CI check `check_settings_tiers.py` (introduced by the slop-removal B4 change) MUST fail when a field declares no tier.

#### Scenario: Replica question places a per-instance value in the environment

- **GIVEN** a new setting whose correct value differs between two replicas (for example an advertise URL)
- **WHEN** the setting is added to `Settings`
- **THEN** it is declared T1 and lives only in the environment

#### Scenario: Replica question places a shared runtime knob in the dashboard

- **GIVEN** a new setting that must hold the same value on every replica and does not need to exist before the database is reachable (for example a stream idle timeout)
- **WHEN** the setting is added
- **THEN** it is declared T3 and is stored in `dashboard_settings` (or another database configuration table)

#### Scenario: Field without a tier fails CI

- **WHEN** a `Settings` field is added without a `tier` in its field metadata
- **THEN** `check_settings_tiers.py` fails and the PR is blocked until the tier is declared

### Requirement: Precedence is code default, then environment, then dashboard

For every T3 setting the effective value MUST be resolved as: the dashboard value when it is non-NULL; otherwise the environment value when the setting has an environment fallback and the variable is set; otherwise the code default. An environment value MUST NOT override a non-NULL dashboard value, and no code path MAY invert this order (environment-wins kill switches, environment values that gate whether a dashboard value is honoured, sentinel dashboard values that defer to the environment, or `max()`/`min()` merges of environment and dashboard values are all prohibited). Where another capability specification currently mandates such an inversion (the `telemetry` environment kill switch, the `rate-limit-reset-credits` polling toggle that gates `auto_redeem_reset_credits_before_expiry`), that specification MUST be amended to this precedence in the same change that removes the inversion from code; until then the inversion is a tracked defect, not an exception to this requirement.

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

For every T3 setting, `GET /api/settings` MUST expose an object with `value` (the effective value), `source` (`"default"`, `"env"` or `"dashboard"`), `env_value` (the environment value, or the code default when the variable is unset) and `default` (the code default). For a T3 setting without an environment fallback (a database-only column) `env_value` MUST be omitted and `source` MUST be `"default"` when the stored value equals the code default and `"dashboard"` otherwise. `PUT /api/settings` MUST accept a concrete value to set the dashboard column and `null` to clear it: for a nullable override column `null` sets the column to NULL and the setting returns to inheritance; for a NOT NULL database-only column `null` resets the column to the code default. The dashboard MUST show a T3 setting whose `source` is not `"dashboard"` as inherited. Pre-existing flat fields (`<name>`, `<name>_environment_value`, `<name>_override`) MAY be exposed alongside the object for one stable release and then removed.

#### Scenario: Provenance of an operator-set value

- **GIVEN** a T3 setting with an environment fallback whose dashboard column is non-NULL
- **WHEN** `GET /api/settings` is called
- **THEN** the setting's `source` is `"dashboard"`, `value` equals the dashboard value, and `env_value` and `default` are reported alongside

#### Scenario: Clearing returns to inheritance

- **GIVEN** a T3 setting with an environment fallback and a non-NULL dashboard value
- **WHEN** `PUT /api/settings` sets it to `null`
- **THEN** the dashboard column becomes NULL and a subsequent `GET` reports `source` as `"env"` (variable set) or `"default"` (variable unset)

#### Scenario: Resetting a database-only setting

- **GIVEN** a T3 setting stored in a NOT NULL column with no environment fallback (for example `warmup_model`)
- **WHEN** `PUT /api/settings` sets it to `null`
- **THEN** the column holds the code default and a subsequent `GET` reports `source: "default"` with no `env_value`

### Requirement: T3 settings have a database home

Every `Settings` field declared T3 MUST have a corresponding `dashboard_settings` column (or a column in another database configuration table), or MUST carry an explicit `migrating_until: "<version>"` marker in its field metadata naming the release by which the migration completes. `check_settings_tiers.py` MUST fail for a T3 field with neither, and for a `migrating_until` marker whose version is at or below the version being built. A PR MUST NOT add a new T3 field that lives only in the environment.

#### Scenario: New environment-only tunable is rejected

- **WHEN** a PR adds a `Settings` field declared T3 without a database column and without `migrating_until`
- **THEN** `check_settings_tiers.py` fails

#### Scenario: Migration marker expires

- **GIVEN** a T3 field carrying `migrating_until: "1.26.0"`
- **WHEN** the version being built is `1.26.0` or later and the field still has no database column
- **THEN** `check_settings_tiers.py` fails

### Requirement: Process environment is read only in the settings module

Under `app/`, `os.environ`, `os.getenv` and `dotenv_values` MUST be accessed only in `app/core/config/settings.py`. Any environment variable the application consumes MUST be a `Settings` field with a declared tier, so that it appears in the generated settings reference and is covered by the removed-settings warning when retired.

#### Scenario: Ad-hoc environment read fails lint

- **WHEN** a module under `app/` other than `app/core/config/settings.py` calls `os.environ`, `os.getenv` or `dotenv_values`
- **THEN** the architecture check in `make lint` fails

#### Scenario: Promoted variable appears in the reference

- **GIVEN** an environment variable previously read outside `Settings`
- **WHEN** it is promoted to a `Settings` field with a tier
- **THEN** `scripts/generate_settings_reference.py` lists it with its tier and the reference test passes

### Requirement: Operator-facing environment documentation lists T0 and T1 only

`.env.example` and `docs/configuration.md` MUST list only T0 and T1 settings. The generated `docs/reference/settings.md` SHALL list every `Settings` field with its tier. `check_settings_tiers.py` MUST fail when `.env.example` contains a variable whose tier is T2, T3 or T4.

#### Scenario: Tunable added to the sample env file

- **WHEN** a PR adds a T3 variable to `.env.example`
- **THEN** `check_settings_tiers.py` fails and the value is documented as a dashboard setting instead

#### Scenario: Reference shows the tier

- **WHEN** `scripts/generate_settings_reference.py` runs
- **THEN** every listed variable carries its tier

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
