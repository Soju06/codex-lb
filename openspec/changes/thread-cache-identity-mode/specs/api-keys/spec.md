# api-keys delta

## ADDED Requirements

### Requirement: API key overrides the thread cache identity mode

An API key SHALL carry an optional thread cache identity override with the same
value domain as the fleet setting (`shared` or `isolated`) and a NULL default
meaning "follow the fleet". The effective mode for a request SHALL be resolved
as: the key's override when it is non-NULL; otherwise the non-NULL
`dashboard_settings` value; otherwise the environment value; otherwise the
`shared` code default. No layer may invert this order.

The write path MUST reject an unrecognised override value with a validation
error naming the accepted values. The read path MUST tolerate a stale or
hand-edited database value by reporting no override rather than failing the
request or the key listing.

The override MUST be resolved from the `api_keys` row rather than carried in
per-request state, so every instance holding that row resolves the same mode
for the same key.

#### Scenario: Key override beats the fleet setting

- **GIVEN** `dashboard_settings.thread_cache_identity_mode` is `shared`
- **AND** an API key's override is `isolated`
- **WHEN** a request authenticated with that key is routed
- **THEN** the effective mode is `isolated` and the request-shape trace records
  that it came from the key override

#### Scenario: Null override follows the fleet

- **GIVEN** an API key with a NULL override
- **WHEN** the dashboard value is `isolated`
- **THEN** the effective mode is `isolated`
- **AND WHEN** the dashboard value is NULL and the environment variable is
  unset
- **THEN** the effective mode is `shared`

#### Scenario: Unrecognised override is rejected on write

- **WHEN** an API key is created or updated with an override that is neither
  `shared` nor `isolated`
- **THEN** the request fails validation and names the accepted values

#### Scenario: Stale stored override does not break the key listing

- **GIVEN** an `api_keys` row whose override column holds a value the current
  release does not recognise
- **WHEN** the key is read
- **THEN** it reports no override and follows the fleet, instead of failing

#### Scenario: Override is resolved from the key row

- **GIVEN** an API key whose override is `isolated`
- **WHEN** any instance resolves the effective mode for a request on that key
- **THEN** it reads the override from the key row and resolves `isolated`,
  independently of per-request state
