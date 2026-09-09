## ADDED Requirements

### Requirement: Account concurrency cap provenance is reported

The settings API MUST report, for each of `proxy_account_response_create_limit`, `proxy_account_stream_limit`, `proxy_account_stream_recovery_reserve` and `proxy_api_key_fair_share_congestion_threshold_pct`, a `provenance` entry with `source` (`"dashboard"`, `"env"` or `"default"`), `env_value` and `default` as defined by `configuration-tiers`, alongside the existing effective value, `<name>_environment_value` and `<name>_override` fields, which MUST remain unchanged. The effective value in the response and the `source` MUST come from the same resolution, so the cap the admission path enforces is the cap the provenance describes. The routing settings MUST show each cap's provenance next to its input and MUST let the operator clear a dashboard-owned cap back to inheritance without typing the environment value. When clearing the stream limit or the stream recovery reserve would leave the effective reserve above a bounded effective stream limit — the combination `PUT /api/settings` rejects — the dashboard MUST disable that cap's reset action and state the reason instead of sending a request that fails.

#### Scenario: Inherited cap is labelled with its layer

- **GIVEN** the response-create limit column is NULL and `CODEX_LB_PROXY_ACCOUNT_RESPONSE_CREATE_LIMIT=6` differs from the code default 4
- **WHEN** an operator opens routing settings
- **THEN** the response-create input is empty and labelled as inherited from the environment with the value 6, while a cap whose environment value equals the code default is labelled as the default

#### Scenario: Dashboard-owned cap can be cleared in one action

- **GIVEN** the stream limit is stored as 24 in `dashboard_settings`
- **WHEN** the operator activates "Reset to inherited" next to the stream limit
- **THEN** the dashboard sends `PUT /api/settings` with `proxyAccountStreamLimit: null`, the column becomes NULL, and the refreshed provenance reports `source` `"env"` or `"default"` with the effective cap that admission now enforces

#### Scenario: Reset that the API would reject is disabled

- **GIVEN** the stream limit is stored as 24 and the recovery reserve as 9 while the inherited stream limit is 8
- **WHEN** the operator opens routing settings
- **THEN** the stream limit's "Reset to inherited" action is disabled with the reason that the reserve would exceed the limit, while the reserve's reset (which would clear it to 1) stays enabled
