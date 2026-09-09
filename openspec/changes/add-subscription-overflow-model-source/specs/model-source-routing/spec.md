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

### Requirement: Model-source pins are durable, thread-keyed and drain-capped

The pin table `model_source_pins` SHALL record a conversation's stickiness to a subscription-overflow model source as rows of three kinds, each namespaced in the primary key: thread pins (`thread\n<key>`), anchors (`anchor\n<api_key_id or ->\n<response_id>`) and WebSocket bounce rows (`bounce\n<key>`). A thread pin's `<key>` MUST be the `thread_only` thread selection key derived from the client's `thread-id` alone: the pin primitive MUST reject the `process-thread` form and MUST NOT namespace thread pins by API key or session, so the same conversation resolves to the same row from every Codex process and API key. Every thread or anchor write at time `now` MUST set `expires_at = min(now + 7 d, drain_until - 21 d - 1 d)` while a drain deadline is armed (`now + 7 d` otherwise) and `purge_at = expires_at + 21 d`, so `purge_at < drain_until` holds for every row written while draining; a bounce row MUST set `expires_at = purge_at = now + 60 s` under the same cap. Re-writing an existing key MUST keep `created_at` and slide `last_seen_at`, `expires_at`, `purge_at`, `source_id` and `api_key_id`; a touch MUST slide a live thread or anchor row only and MUST NOT revive a tombstone or slide a bounce row. A lookup at `now` MUST classify a row as `live` (`expires_at > now`), `expired` (`expires_at <= now < purge_at`, a tombstone), `bounce` (a bounce row with `purge_at > now`) or `none` (absent or `purge_at <= now`). Lookups MAY be served from a per-replica positive-only cache (60 s TTL, at most 10 000 entries) that stores live records only: absence and non-live states MUST NOT be cached, and a cached record that no longer classifies as live MUST be dropped and re-read. A bounded lookup MUST issue at most one primary-key read and MUST fail with a lookup timeout after 2 s. Pin timestamps MUST be stored and compared as UTC on both SQLite and PostgreSQL. In this stage the pin primitive has no request-path caller: the request path MUST NOT read the pin table until the overflow routing requirements are specified.

#### Scenario: Same conversation from another process or API key

- **GIVEN** a Codex conversation with `thread-id` `t1` seen with session `s1` through API key `k1`
- **WHEN** the same `thread-id` is presented with session `s2` or through API key `k2`
- **THEN** the thread pin key is identical
- **AND** building a thread pin key from the `process-thread` selection key is rejected

#### Scenario: Lookup states over a pin's lifetime

- **GIVEN** a thread pin written at `T` with no drain armed
- **THEN** a lookup at `T + 6 d` is `live`, at `T + 7 d` is `expired`, at `T + 27 d` is `expired` and at `T + 28 d` is `none`
- **AND** a bounce row written at `T` is `bounce` at `T` and `none` at `T + 60 s`

#### Scenario: Re-writes slide, touches never revive

- **GIVEN** a thread pin written at `T`
- **WHEN** it is re-written at `T + 1 d` with another source and touched at `T + 2 d`
- **THEN** `created_at` is still `T`, `last_seen_at` is `T + 2 d` and `expires_at` is `T + 9 d`
- **AND** a touch after `expires_at` changes nothing and reports no row changed
- **AND** a touch of a bounce row changes nothing and reports no row changed

#### Scenario: Writes during a drain never outlive the deadline

- **GIVEN** a thread pin written three days before an operator clears the designation at `T` (`drain_until = T + 29 d`)
- **WHEN** the pin is touched every day
- **THEN** `expires_at` never exceeds `T + 7 d` and `purge_at` stays below `drain_until`
- **AND** the pin is `live` until day 7, `expired` until day 28 and `none` from day 28 on
- **AND** no row answers a lookup at `drain_until`

#### Scenario: Absence is never cached

- **GIVEN** a bounded lookup with a positive cache found no row for a thread
- **WHEN** another replica writes the pin and the lookup is repeated
- **THEN** the second lookup reads the table again and returns `live`
- **AND** a cached record whose `expires_at` has passed is dropped and the table is re-read

#### Scenario: Lookup deadline

- **GIVEN** the database does not answer the primary-key read within 2 s
- **WHEN** a bounded lookup runs
- **THEN** it fails with a lookup timeout instead of waiting

### Requirement: Pin writes are verified durable

A pin write SHALL bound only the acquisition of the write path (the SQLite writer section, then the session's connection checkout) by a 10 s deadline; a deadline reached, or any failure raised, before the statement is issued MUST yield `not_written` with no statement issued (a caller cancellation before issuance simply propagates). Once issued, the statement and its COMMIT MUST run to completion even when the requesting client cancels: the cancellation MUST be deferred until the outcome is known and logged, then honoured. A statement or COMMIT failure MUST be resolved by a primary-key re-read bounded by 2 s: rows that reflect the write yield `written`, no such rows yield `not_written`, and a failing or timed-out re-read yields `unknown`. Every non-`written` outcome MUST be logged at WARN as `model_source_pin_write outcome=<outcome>`. The neutral release of a pin MUST use the same discipline (`written` means the row is verifiably gone) and MUST invalidate any positive cache entry for the key before issuing the delete. SQLite divergence: the acquisition deadline covers the in-process writer queue only; an external writer holding the database file may hold an issued statement for up to the driver's 30 s busy timeout before the outcome is known, whereas PostgreSQL bounds the wait at checkout.

#### Scenario: Acquisition timeout or failure

- **GIVEN** the writer section cannot be acquired for 10 s, or the connection checkout raises
- **WHEN** a pin write is committed
- **THEN** the outcome is `not_written`, no statement was issued and `model_source_pin_write outcome=not_written` is logged

#### Scenario: Client cancels while the statement is in flight

- **GIVEN** a pin write whose statement has been issued
- **WHEN** the requesting task is cancelled before the statement completes
- **THEN** the statement and COMMIT complete, the outcome `written` is logged, and the cancellation is raised to the caller afterwards

#### Scenario: Post-issuance failure resolved by re-read

- **GIVEN** the statement or COMMIT raised after issuance
- **WHEN** the primary-key re-read finds rows reflecting the write
- **THEN** the outcome is `written`
- **AND** when it finds no such rows the outcome is `not_written`

#### Scenario: Unresolvable outcome

- **GIVEN** the statement raised after issuance and the re-read fails or exceeds 2 s
- **WHEN** the write is resolved
- **THEN** the outcome is `unknown` and `model_source_pin_write outcome=unknown` is logged

#### Scenario: Concurrent writers on SQLite

- **GIVEN** 50 concurrent pin writes against a file-backed SQLite database
- **WHEN** they run through the writer section
- **THEN** every write is `written` and the p99 write latency stays under 5 s
