## MODIFIED Requirements

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
