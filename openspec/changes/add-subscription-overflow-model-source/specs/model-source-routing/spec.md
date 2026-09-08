## ADDED Requirements

### Requirement: Operators designate a subscription-overflow model source

The dashboard settings SHALL persist an optional subscription-overflow designation, `subscription_overflow_source_id`, and a read-only drain deadline, `subscription_overflow_drain_until`, and expose both on the settings read contract together with the derived read-only `subscription_overflow_pins_expire_by`: the deadline minus the 21-day tombstone grace and one day (the clear time plus the 7-day pin idle limit), or `null` when no drain is armed. Because the drain cap bounds every pin's expiry to that instant, it is the date the dashboard's drain notice MUST show and MUST be gated on; the notice MUST NOT present `subscription_overflow_drain_until` as the time conversations keep working. The settings update contract MUST treat `subscription_overflow_source_id` as tri-state: an omitted field MUST leave the stored designation untouched, an explicit `null` MUST clear it, and a value MUST designate that source. A new designation MUST be accepted only when it names an existing model source of kind `openai_compatible` that declares the Responses capability; otherwise the update MUST fail with HTTP `400` and error code `subscription_overflow_source_invalid` and MUST NOT change any stored setting. The source's enabled state MUST NOT be validated. When a stored designation is cleared, the same row update MUST set `subscription_overflow_drain_until` to the write time plus 29 days (the 7-day pin idle limit, the 21-day tombstone grace, and one day); when a designation is written while none is stored, the same row update MUST set the deadline to `null`; re-sending the stored value or switching between two sources MUST NOT change the deadline. Every accepted write MUST invalidate the dashboard-settings cache on every replica and MUST report `subscription_overflow_source_id` and, when it changed, `subscription_overflow_drain_until` in the `settings_changed` audit entry's changed fields. Deleting the designated model source MUST clear the designation and arm the drain deadline in the same database transaction as the delete, MUST invalidate the dashboard-settings cache after that transaction commits, and MUST NOT touch the designation when a different or unknown source is deleted. Until the overflow routing requirements are specified, no request-path component MAY read the designation or the deadline, and an exhausted subscription pool MUST keep answering exactly as it does without a designation.

#### Scenario: Operator designates a Responses-capable source

- **GIVEN** an enabled model source of kind `openai_compatible` with the Responses capability
- **WHEN** an operator updates settings with `subscription_overflow_source_id` set to that source's id
- **THEN** the response and subsequent settings reads carry that id
- **AND** `subscription_overflow_drain_until` is `null`

#### Scenario: Chat-only or unknown sources are rejected

- **WHEN** an operator updates settings with `subscription_overflow_source_id` naming a source without the Responses capability, or an id that does not exist
- **THEN** the update fails with HTTP `400` and error code `subscription_overflow_source_invalid`
- **AND** the stored designation is unchanged

#### Scenario: Clearing the designation arms the drain deadline

- **GIVEN** a stored designation
- **WHEN** an operator updates settings with `subscription_overflow_source_id` set to `null`
- **THEN** the stored designation is `null`
- **AND** `subscription_overflow_drain_until` is the write time plus 29 days
- **AND** `subscription_overflow_pins_expire_by` is the write time plus 7 days
- **AND** a second update with `null` leaves that deadline unchanged

#### Scenario: Re-designating during the drain clears the deadline

- **GIVEN** no stored designation and an armed drain deadline
- **WHEN** an operator designates an eligible source
- **THEN** `subscription_overflow_drain_until` is `null`
- **AND** `subscription_overflow_pins_expire_by` is `null`

#### Scenario: Partial updates and source switches leave the deadline alone

- **GIVEN** a stored designation
- **WHEN** an operator updates unrelated settings without the field, re-sends the same designation, or designates a different eligible source
- **THEN** the designation is preserved, re-stored, or switched respectively
- **AND** `subscription_overflow_drain_until` is unchanged

#### Scenario: Deleting the designated source clears the designation

- **GIVEN** a stored designation
- **WHEN** an operator deletes that model source
- **THEN** the delete succeeds and the stored designation is `null`
- **AND** `subscription_overflow_drain_until` is the delete time plus 29 days
- **AND** `subscription_overflow_pins_expire_by` is the delete time plus 7 days
- **AND** the dashboard-settings cache is invalidated after the delete commits

#### Scenario: Deleting another source leaves the designation alone

- **GIVEN** a stored designation
- **WHEN** an operator deletes a different model source, or requests deletion of an unknown source id
- **THEN** the stored designation and deadline are unchanged

#### Scenario: A designation does not change the exhausted-pool answer

- **GIVEN** a stored designation naming a source that serves the requested registry model
- **AND** every eligible subscription account is usage-exhausted
- **WHEN** a client sends a Responses request for that model
- **THEN** the response is HTTP `429` with `error.code` `usage_limit_reached` and the pool's `resets_at`
- **AND** the designated source receives no request

### Requirement: Preflight reports overflow readiness without blocking

The dashboard API SHALL expose `GET /api/settings/subscription-overflow/preflight?source_id=<id>` for sessions with dashboard write access. It MUST return HTTP `404` for an unknown source id and otherwise MUST return a report that never fails for an ineligible source: `eligible` and `blockers` (only `source_kind_unsupported` and `source_responses_unsupported`), the source's enabled state, the current drain deadline, the served models with per-model readiness, the missing models, the number of API keys scoped to the source, and the live and tombstone thread-pin counts. For each model listed on the source the report MUST state whether it can never overflow in this version (a registry model served through Responses-Lite or code mode, or a slug unknown to the subscription registry) and otherwise MUST warn about undeclared Codex tool types (`custom`, `apply_patch`, `web_search`, `shell`, `local_shell`, `tool_search` not declared on the model entry), missing vision, missing streaming, missing pricing, and a context window that is missing or smaller than the registry's, reporting the registry and source windows. `missing_models` MUST list the subscription registry slugs that could overflow and are not enabled on the source. Warnings MUST NOT block a designation.

#### Scenario: Chat-only source reports a blocker

- **GIVEN** a model source without the Responses capability
- **WHEN** an operator requests its preflight
- **THEN** the response is HTTP `200` with `eligible` false and `blockers` containing `source_responses_unsupported`
- **AND** the served models are still reported

#### Scenario: Served and missing models with warnings

- **GIVEN** an eligible source listing a registry model with an 8192-token context window, no vision, no output pricing, and only some tool types declared, plus a Responses-Lite registry model
- **WHEN** an operator requests its preflight
- **THEN** the registry model reports the undeclared tool types, `no_vision`, `unpriced`, and `context_window_smaller` with the registry and source windows
- **AND** the Responses-Lite model reports `never_overflows` with its reason and no other warning
- **AND** `missing_models` lists the other overflow-eligible registry slugs and omits the Responses-Lite family

#### Scenario: Scoped keys and pins are counted

- **GIVEN** one API key scoped to the source, one live thread pin, one tombstoned thread pin, and one purged thread pin on the source
- **WHEN** an operator requests its preflight
- **THEN** `scoped_api_key_count` is 1, `live_pin_count` is 1, and `tombstone_count` is 1

#### Scenario: Unknown source and read-only access

- **WHEN** a read-only dashboard session requests a preflight, or any session requests one for an unknown source id
- **THEN** the response is HTTP `403` or HTTP `404` respectively
