## ADDED Requirements

### Requirement: Settings API exposes provenance as an additive map

`GET /api/settings` and the response of `PUT /api/settings` MUST include `provenance`, a map keyed by setting name (the `dashboard_settings` column name, which is also the `Settings` field name for settings with an environment fallback) whose entries carry `source` (`"dashboard"`, `"env"` or `"default"`), `env_value` and `default`. The effective value stays in the existing top-level field of the same name; the map adds no second copy of it. Every inheritable setting — a setting whose NULL dashboard column falls back to the environment or to the code default — MUST have an entry. `source` MUST be computed by the single resolver of `SettingsService` as: `"dashboard"` when the column is non-NULL (including an explicit `0` or `false`); otherwise `"env"` when the setting has an environment fallback and the environment value differs from the code default; otherwise `"default"`. `env_value` MUST be the process environment value (the code default when the variable is unset) for a setting with an environment fallback and `null` for a database-only setting. `env_value` and `default` carry the setting's own scalar type. Adding `provenance` MUST NOT change any pre-existing response field; a client that does not know the field MUST keep working, and the dashboard MUST keep working against a backend that omits it.

The dashboard MUST render a setting whose `source` is not `"dashboard"` as inherited, naming the layer and the value it inherits ("inherited from environment (12)", "default (8)"), and MUST offer a "reset to inherited" action for a setting whose `source` is `"dashboard"`; the action sends the existing tri-state `PUT /api/settings` with an explicit `null` for that setting and nothing else changed.

#### Scenario: Environment value differs from the default

- **GIVEN** `proxy_account_stream_limit` is NULL in `dashboard_settings` and `CODEX_LB_PROXY_ACCOUNT_STREAM_LIMIT=12` while the code default is 8
- **WHEN** `GET /api/settings` is called
- **THEN** `proxyAccountStreamLimit` is 12 and `provenance.proxy_account_stream_limit` is `{"source": "env", "envValue": 12, "default": 8}`

#### Scenario: Environment value equals the default

- **GIVEN** the same column is NULL and the variable is unset or set to 8
- **WHEN** `GET /api/settings` is called
- **THEN** `provenance.proxy_account_stream_limit` is `{"source": "default", "envValue": 8, "default": 8}`

#### Scenario: Operator-set value

- **GIVEN** an operator has set the stream limit to 24 through `PUT /api/settings`
- **WHEN** `GET /api/settings` is called
- **THEN** `proxyAccountStreamLimit` is 24 and `provenance.proxy_account_stream_limit` is `{"source": "dashboard", "envValue": 8, "default": 8}`

#### Scenario: Database-only setting

- **GIVEN** `request_log_retention_days` has no environment fallback and its column is NULL
- **WHEN** `GET /api/settings` is called
- **THEN** `provenance.request_log_retention_days` is `{"source": "default", "envValue": null, "default": 0}`; once an operator stores `0` the entry reports `source: "dashboard"`

#### Scenario: Reset to inherited from the dashboard

- **GIVEN** the routing settings show a stream limit whose `source` is `"dashboard"`
- **WHEN** the operator activates "Reset to inherited"
- **THEN** the dashboard sends `PUT /api/settings` with `proxyAccountStreamLimit: null` and the other fields unchanged, and the refreshed response reports `source` `"env"` or `"default"` for the stream limit

#### Scenario: Dashboard against an older backend

- **GIVEN** a backend response without `provenance`
- **WHEN** the dashboard parses it
- **THEN** parsing succeeds and the capacity inputs fall back to the effective-value hint derived from the flat `<name>EnvironmentValue` fields
