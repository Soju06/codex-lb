## ADDED Requirements

### Requirement: Operators designate a subscription-overflow model source

The dashboard settings SHALL persist an optional subscription-overflow designation, `subscription_overflow_source_id`, and a read-only drain deadline, `subscription_overflow_drain_until`, and expose both on the settings read contract together with the derived read-only `subscription_overflow_pins_expire_by`: the deadline minus the 21-day tombstone grace and one day (the clear time plus the 7-day pin idle limit), or `null` when no drain is armed. Because the drain cap bounds every pin's expiry to that instant, it is the date the dashboard's drain notice MUST show and MUST be gated on; the notice MUST NOT present `subscription_overflow_drain_until` as the time conversations keep working. The settings update contract MUST treat `subscription_overflow_source_id` as tri-state: an omitted field MUST leave the stored designation untouched, an explicit `null` MUST clear it, and a value MUST designate that source. A new designation MUST be accepted only when it names an existing model source of kind `openai_compatible` that declares the Responses capability; otherwise the update MUST fail with HTTP `400` and error code `subscription_overflow_source_invalid` and MUST NOT change any stored setting. The source's enabled state MUST NOT be validated. When a stored designation is cleared, the same row update MUST set `subscription_overflow_drain_until` to the write time plus 29 days (the 7-day pin idle limit, the 21-day tombstone grace, and one day); when a designation is written while none is stored, the same row update MUST set the deadline to `null`; re-sending the stored value or switching between two sources MUST NOT change the deadline. Every accepted write MUST invalidate the dashboard-settings cache on every replica and MUST report `subscription_overflow_source_id` and, when it changed, `subscription_overflow_drain_until` in the `settings_changed` audit entry's changed fields. Deleting the designated model source MUST clear the designation and arm the drain deadline in the same database transaction as the delete, MUST invalidate the dashboard-settings cache after that transaction commits, and MUST NOT touch the designation when a different or unknown source is deleted. The request path MUST read the designation and the deadline only from the dashboard-settings row it already loads through the warm settings cache, as the first step of every overflow entry point (two attribute reads and one comparison), and while both are `null` it MUST perform no exhaustion probe, no pin or anchor lookup, no source or model selection, no admission claim and no request-body walk on behalf of overflow, so an exhausted subscription pool answers byte-identically to a deployment without the overflow feature and a served subscription stream is byte-identical as well (the ship-dark guarantee; the routing requirements below define what the request path does once a designation is stored or a drain deadline is armed). The designation is a fleet-wide row: there is no per-replica flag or environment variable, and every replica observes a change within the settings-cache invalidation window.

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

#### Scenario: Without a designation or drain deadline the exhausted-pool answer is untouched

- **GIVEN** `subscription_overflow_source_id` and `subscription_overflow_drain_until` are both `null`
- **AND** every eligible subscription account is usage-exhausted
- **WHEN** a client sends a Responses request
- **THEN** the response is byte-identical (status, headers other than date and request id, body) to the answer of a deployment without the overflow feature: HTTP `429` with `error.code` `usage_limit_reached` and the pool's `resets_at`
- **AND** no exhaustion probe, pin lookup, source selection, portability view or admission claim ran and the request body was not dumped for forwarding
- **AND** a successful subscription stream for a healthy pool, native and SDK shaped, is byte-identical as well

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

The pin table `model_source_pins` SHALL record a conversation's stickiness to a subscription-overflow model source as rows of three kinds, each namespaced in the primary key: thread pins (`thread\n<key>`), anchors (`anchor\n<api_key_id or ->\n<response_id>`) and WebSocket bounce rows (`bounce\n<key>`). A thread pin's `<key>` MUST be the `thread_only` thread selection key derived from the client's `thread-id` alone: the pin primitive MUST reject the `process-thread` form and MUST NOT namespace thread pins by API key or session, so the same conversation resolves to the same row from every Codex process and API key. Every thread or anchor write at time `now` MUST set `expires_at = min(now + 7 d, drain_until - 21 d - 1 d)` while a drain deadline is armed (`now + 7 d` otherwise) and `purge_at = expires_at + 21 d`, so `purge_at < drain_until` holds for every row written while draining; a bounce row MUST set `expires_at = purge_at = now + 60 s` under the same cap. Re-writing an existing key MUST keep `created_at` and slide `last_seen_at`, `expires_at`, `purge_at`, `source_id` and `api_key_id`; a touch MUST slide a live thread or anchor row only and MUST NOT revive a tombstone or slide a bounce row. A lookup at `now` MUST classify a row as `live` (`expires_at > now`), `expired` (`expires_at <= now < purge_at`, a tombstone), `bounce` (a bounce row with `purge_at > now`) or `none` (absent or `purge_at <= now`). Lookups MAY be served from a per-replica positive-only cache (60 s TTL, at most 10 000 entries) that stores live records only: absence and non-live states MUST NOT be cached, and a cached record that no longer classifies as live MUST be dropped and re-read. A bounded lookup MUST issue at most one primary-key read and MUST fail with a lookup timeout after 2 s. Pin timestamps MUST be stored and compared as UTC on both SQLite and PostgreSQL. The pin primitive's request-path callers are exactly the overflow admission decision (thread pin, then anchor), the pinned-conversation compaction check on both compact routes, the WebSocket handshake denial and the WebSocket in-band bounce helpers; each performs at most one bounded thread-pin lookup and at most one bounded anchor lookup per request, and none of them runs while the designation and the drain deadline are both `null`. Routing coupling, stated here and in the operator documentation: while a designation is stored or a drain deadline is armed, the routing of every native Codex request carrying `thread-id` and of every request carrying `previous_response_id` depends on one bounded indexed point read of `model_source_pins`; a database outage fails those requests closed -- HTTP `503` `model_source_unavailable` with `Retry-After: 2` on the HTTP routes, HTTP `426` at a WebSocket handshake, an in-band bounce on an open WebSocket session -- rather than presenting a possibly source-served transcript to a subscription account, and the Codex retry ladder multiplies the request count against the recovering database. Requests without a thread key and without `previous_response_id` are unaffected. The point read MUST NOT be replaced by a negative cache, a presence hint or a snapshot: only a read-your-writes lookup can see a pin another replica committed milliseconds earlier, and Codex posts tool-call follow-ups within milliseconds of the terminal frame.

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

#### Scenario: Database outage fails thread-keyed requests closed while overflow is enabled

- **GIVEN** a stored designation and a database that does not answer the pin read
- **WHEN** a native Codex request carrying `thread-id` arrives
- **THEN** it is answered HTTP `503` with `error.code` `model_source_unavailable` and `Retry-After: 2` within about 2 s, `pinned_lookup_timeout` is counted and no subscription account is selected
- **AND** a request carrying neither `thread-id` nor `previous_response_id` is served exactly as before
- **WHEN** the designation and the drain deadline are both `null`
- **THEN** no pin read happens and every request is served exactly as before

### Requirement: Pin writes are verified durable

A pin write SHALL bound only the acquisition of the write path (the SQLite writer section, then the session's connection checkout) by a 10 s deadline; a deadline reached, or any failure raised, before the statement is issued MUST yield `not_written` with no statement issued (a caller cancellation before issuance simply propagates). Once issued, the statement and its COMMIT MUST run to completion even when the requesting client cancels: the cancellation MUST be deferred until the outcome is known and logged, then honoured. A statement or COMMIT failure MUST be resolved by a primary-key re-read bounded by 2 s: rows that reflect the write yield `written`, no such rows yield `not_written`, and a failing or timed-out re-read yields `unknown`. Every non-`written` outcome MUST be logged at WARN as `model_source_pin_write outcome=<outcome>`. The neutral release of a pin MUST use the same discipline (`written` means the row is verifiably gone) and MUST invalidate any positive cache entry for the key before issuing the delete. SQLite divergence: the acquisition deadline covers the in-process writer queue only; an external writer holding the database file may hold an issued statement for up to the driver's 30 s busy timeout before the outcome is known, whereas PostgreSQL bounds the wait at checkout. A pin intent that owes an anchor -- every dispatch whose client did not send `store: false` -- SHALL NOT be committed without it: when the source has minted no `response.id` by the content trigger (streaming) or in the answer JSON (non-streaming), the write MUST yield `not_written` with no statement issued, so no thread pin is written either and the turn fails closed through the pin-failure lifecycle, and the WARN line MUST name `reason=anchor_unresolved`. An intent that owes no anchor (`store: false`, i.e. every native Codex turn) MUST still commit its thread pin alone, and an intent with nothing to write because its evidence is already durable (a pinned or anchored continuation) MUST stay vacuously `written`.

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

#### Scenario: An owed anchor without a source response id is never written

- **GIVEN** a dispatch whose client did not send `store: false`
- **WHEN** its first content-bearing frame arrives before the source has minted a `response.id`
- **THEN** the pin write is `not_written` with no statement issued, no thread pin row exists, and the WARN line names `reason=anchor_unresolved`
- **WHEN** the same dispatch carried `store: false`
- **THEN** the thread pin is written alone and the frame is delivered

### Requirement: Subscription-exhaustion overflow is decided once at route admission

On `/backend-api/codex/responses` and `/v1/responses`, streaming and non-streaming, the proxy SHALL decide subscription-exhaustion overflow exactly once, at route admission: after API-key enforcement, model validation, direct model-source selection and (on the subscription path) the service-tier/model fallback, and before a direct source dispatch, any account selection, usage reservation, lease, HTTP-bridge session or client-visible byte, so the exhaustion probe evaluates the same inputs ordinary selection would. The HTTP Responses session bridge and every other route MUST NOT enter overflow; exhaustion discovered after admission keeps today's terminal answer and the next request overflows (no late fallback). The decision MUST evaluate these steps in this order and every step MUST short-circuit: (1) the settings fast path of the designation requirement; (2) when the request carries `thread-id`, one bounded thread-pin lookup -- a `live` or `expired` pin routes the request through the pinned-conversation requirement regardless of pool state, body portability or a binding `x-codex-turn-state`, because a pin is durable knowledge that the transcript holds source items while a turn state is a client claim about an upstream owner; (3) when the request carries `previous_response_id`, one bounded anchor lookup for the presenting API key -- a live anchor routes to its source, also while draining, and an unknown id continues on the unchanged owner fail-closed path and never overflows (counted `declined_not_portable_history`, without the history hint, before any probe or selection); when direct routing selected a source for the requested model, steps (2) and (3) still run and a live pin or anchor is decided by the pinned-conversation requirement whichever source serves the model directly, while without evidence the decision returns to direct routing right after the lookups -- a model a source serves directly is that source's request, never a subscription exhaustion, so no drain decline, probe, selection or outcome follows and an unanchored `previous_response_id` counts nothing; (4) with no designation stored (drain mode) the request is counted `declined_drain_mode` and falls through; (5) the O(1) declines, in order: `pin_commit_recent_failure` (the thread key was marked by a failed pin commit within the last 60 s), `turn_state_bound` (a binding `x-codex-turn-state`), `opportunistic` (an opportunistic API key), `key_scope` (an API key scoped to model sources), `no_thread_key` (a native Codex request without `thread-id`), `background_job`, `source_excluded` (a request the HTTP route excludes from source routing: a terminal `compaction_trigger` item or uploaded-file references) and `breaker_open` (an advisory read of the per-source breaker); (6) the read-only pool-exhaustion probe, called with the dashboard-settings snapshot read in step 1 and the request's model and requested `service_tier` -- any answer but `usage_limit_reached` falls through, so the trigger is exactly the structured usage-limit predicate and never local capacity caps, fair-share throttles, admission overload, authentication failures, model rejections, transient upstream errors or a single account's rate limit, and under `sequential_drain`, `reset_drain` and `single_account` the probe declines and overflow never triggers in this version; (7) source and model resolution against the designated source only, honouring the key's allowed models and the streaming requirement -- `no_source` when the designated source is missing, disabled or not Responses-capable, `model_unlisted` when it does not serve the model; (8) the forwarding dump stripped of telemetry with `service_tier` removed and the client's own `store` restored (the field is omitted when the client omitted it, so the source applies its default; Codex's `store: false` is forwarded verbatim), the overflow portability view and the provider-portability verdict -- `not_portable_history` is the only decline that sets the request-scoped history hint, every other portability reason is counted `declined_not_portable_input` and logged with its reason and detail at a rate-limited WARN; (9) the admission claims -- the per-source concurrency slot and the breaker token -- taken synchronously as the last step, with no await and no fallible call after them; a denial is `declined_source_busy` or `declined_breaker_open`. The background-job check MUST be a closed allowlist: `x-openai-subagent` absent or equal to `review`, `compact` or `collab_spawn` is allowed; any other value is `background_job`, and a request carrying `x-openai-memgen-request` is `background_job` whatever the subagent header says. Every decline MUST fall through to the unchanged subscription path, which answers today's `429` `usage_limit_reached` rebuilt from the same selection (byte-identical except the history hint); a fresh request is never answered `503` because of overflow. A decision failure (`Exception`, never `CancelledError`) MUST be counted `decision_error`, logged at WARN as `subscription_overflow_decision_error stage=<stage>` with the request id, and fall through -- except in a pinned or anchored context, where a pin or anchor was found or a lookup failed or timed out while the request carried a thread key or `previous_response_id` and lookups were mandated: there the request MUST be answered HTTP `503` `model_source_unavailable` with `Retry-After: 2` (`pinned_lookup_timeout` when the lookup timed out), because falling through would present source ciphertext to a subscription account. A fresh dispatch MUST carry the retained exhaustion answer (`resets_at`, selection), the stripped body, the claims, the thread key, a pin intent that writes the thread pin when a thread key exists and the anchor when `store` is not `false`, the request-log source `subscription_overflow` and the dispatch kind `fresh`; a `CancelledError` anywhere in the decision MUST leave nothing claimed (bulkhead in-flight unchanged, breaker trial free). The dispatch MUST run through the hardened source route with the decision's claims transferred to the dispatch owner, the payload's model replaced by the resolved source model, and a route-helper latch that releases the claims on every exit before an owner exists. The decision module MUST use no timing primitive of its own (deadlines flow through the service's scheduler and clock) and MUST own no task.

#### Scenario: Fresh overflow on an exhausted pool

- **GIVEN** a stored designation naming an enabled source that serves the requested registry model and declares the Codex tool types the body uses
- **AND** every eligible subscription account is usage-exhausted
- **WHEN** a native Codex first turn carrying `thread-id`, without prior reasoning, is sent to `/backend-api/codex/responses` with `stream: true`
- **THEN** the response is HTTP `200` carrying the source's single Responses lifecycle
- **AND** the source received exactly one request whose body has no `client_metadata`, `stream_options` or `service_tier` and carries `prompt_cache_key` verbatim
- **AND** no subscription account was selected, leased or marked and no sticky, turn-state or bridge state was written
- **AND** exactly one request-log row exists with `source` `subscription_overflow`, and `codex_lb_subscription_overflow_total{route="codex_responses",outcome="dispatched_fresh"}` is incremented
- **WHEN** the same pool receives a non-streaming `/v1/responses` request
- **THEN** the response is HTTP `200` with the source's JSON answer and `route="v1_responses"` is counted

#### Scenario: Declines keep today's answer byte for byte

- **GIVEN** a stored designation and an exhausted pool
- **WHEN** a request arrives with `x-openai-subagent: memory_consolidation`, or with `x-openai-memgen-request: true`, or with a binding `x-codex-turn-state`, or from an opportunistic API key, or from a key scoped to model sources, or as a native Codex request without `thread-id`
- **THEN** the response is byte-identical to the `429` `usage_limit_reached` answer of a deployment without a designation
- **AND** the source receives no request and the matching `declined_<reason>` outcome is counted once
- **WHEN** a request arrives with `x-openai-subagent: review`, `compact` or `collab_spawn`, or without the header
- **THEN** the background-job check admits it

#### Scenario: A healthy pool never overflows

- **GIVEN** a stored designation and at least one eligible account with usage headroom
- **WHEN** a request arrives
- **THEN** a subscription account serves it
- **AND** no source query, portability view or admission claim ran

#### Scenario: Drain strategies never trigger overflow

- **GIVEN** a stored designation, an exhausted pool and `single_account`, `sequential_drain` or `reset_drain` as the routing strategy
- **WHEN** a request arrives
- **THEN** the probe declines and the response is today's `429` `usage_limit_reached`

#### Scenario: Portability is judged before anything is claimed

- **GIVEN** the designated source's breaker is `half_open` with its trial available
- **WHEN** a request whose input carries a `reasoning` item is declined `not_portable_history`
- **THEN** the trial is still available and the next portable request becomes the trial
- **AND** the bulkhead in-flight count is unchanged

#### Scenario: Decision failures fall through except in a pinned context

- **GIVEN** a stored designation and a pin lookup that raises
- **WHEN** a request without `thread-id` and without `previous_response_id` arrives
- **THEN** `decision_error` is counted, `subscription_overflow_decision_error` is logged and the response is today's answer
- **WHEN** a request carrying `thread-id` arrives
- **THEN** the response is HTTP `503` `model_source_unavailable` with `Retry-After: 2` and no subscription account is selected

#### Scenario: Cancellation after the claims holds nothing

- **GIVEN** a decision that has taken the concurrency slot and the breaker token
- **WHEN** the request handler is cancelled before the dispatch owner exists
- **THEN** the slot and the token are released, no usage reservation exists and no request-log row is written

#### Scenario: The HTTP bridge never enters overflow

- **GIVEN** a stored designation and an exhausted pool
- **WHEN** a request arrives through the HTTP Responses session bridge
- **THEN** it is answered `429` `usage_limit_reached` immediately and the source receives no request

#### Scenario: Direct source routing consults the pin first

- **GIVEN** thread `t1` is pinned to the overflow source `A` and a second source `B` serves model `m-b` directly, which `A` does not list
- **WHEN** the next turn of `t1` requests `m-b` and its transcript carries source reasoning
- **THEN** the response is HTTP `400` `subscription_overflow_source_unavailable`, neither source receives a request and the pin is kept
- **WHEN** the next turn of `t1` requests `m-b` with a source-free transcript
- **THEN** the pin is deleted durably first and `B` serves the turn directly with the input items' top-level `id` removed
- **WHEN** `A` also serves `m-b`
- **THEN** the turn is dispatched to `A` as a `pinned` dispatch
- **WHEN** a request without pin or anchor evidence asks for `m-b`
- **THEN** `B` serves it directly and no probe, selection or overflow outcome is recorded

### Requirement: Pinned and anchored conversations route to their source or fail closed

A conversation that received content from a subscription-overflow source SHALL keep routing to that source: a `live` thread pin dispatches to the pinned source regardless of pool state and body portability (dispatch kind `pinned`, request-log source `subscription_overflow_pinned`), and a live anchor for the presenting API key and `previous_response_id` dispatches to the anchor's source (dispatch kind `anchor`, the same request-log source), both also while a drain deadline is armed. Pinned and anchored dispatch MUST re-check the presenting key's allowed models, that a key scoped to model sources includes the pinned source in its scope and that the request is not excluded from source routing, and MAY serve any enabled Responses model of the pinned source the key allows (a model switch stays on the source) -- also when another source serves the requested model directly: the pin or anchor is consulted before any direct source dispatch, so a directly owned model the pinned source does not serve follows the unservable rules below instead of reaching the other source with the pinned transcript, and a neutral release hands the id-stripped body to that source's direct route. A pinned or anchored request MUST never reach a subscription account except through the neutral release below and MUST never be answered `429` `usage_limit_reached` because of overflow. Transient obstacles -- the breaker open without an available trial, no concurrency slot, a lookup timeout, a decision failure -- MUST be answered HTTP `503` with `error.type` `upstream_error`: `model_source_unavailable` with `Retry-After: 2`, or `model_source_busy` with `Retry-After: 1` for a saturated source; the answer MUST never use `server_is_overloaded` or `slow_down`. Permanent obstacles -- the source disabled or deleted, the model unlisted on the source, the key scoped to model sources that exclude the pinned source, or a tombstone (`expired`) pin -- MUST first attempt a neutral release: re-read the row by primary key (the positive cache MUST NOT be trusted here, because a concurrent release elsewhere must be seen), evaluate `transcript_is_source_free` on the id-stripped portability view and, when the transcript is source-free, delete the pin durably first (`written` outcome required; any other outcome answers HTTP `503` `model_source_unavailable`), log `subscription_overflow_pinned_released_neutral`, remove the top-level `id` of every input item from the body handed on (the only body mutation the feature performs there), count `pinned_released_neutral` and serve the request on the subscription path. When the transcript is not source-free the request MUST be refused with HTTP `400`, `error.type` `invalid_request_error`: `subscription_overflow_source_unavailable` for a live pin ("this conversation was served by an overflow model source that is no longer available; start a new conversation"; counted `pinned_unservable_source_disabled`, `pinned_unservable_source_deleted` or `pinned_unservable_model_unlisted`) and `subscription_overflow_unsupported_input` for a tombstone ("conversation expired"; counted `pinned_unservable_tombstone`). A live pinned conversation whose request the source cannot take -- a terminal `compaction_trigger` item, a compact route, uploaded-file references, or an `input_image` without the source model's vision -- MUST be refused with HTTP `400` `subscription_overflow_unsupported_input` without releasing the pin, before any claim; the image variant of the message names images (remove the image or start a new conversation), the others name file references and compaction, and declaring `supports_vision` on the source model restores the dispatch. Pins and anchors MUST be written only at the content trigger of a dispatch, in one transaction, verified durable before the first content-bearing frame (streaming) or before the JSON answer (non-streaming) as the pin-write requirement states: a fresh dispatch with a thread key writes the thread pin; every dispatch with `store` not `false` (the client's own value, captured before the ChatGPT validator forces the field to `false`; an omitted `store` counts as stored) writes the anchor `anchor\n<api_key_id or ->\n<response_id>` resolved from the source's `response.id` at that moment (`store: false` writes no anchor) -- a source that has minted no `response.id` at that moment cannot be anchored, so the turn MUST fail closed as the pin-write requirement states instead of being delivered unanchored, while a native thread-keyed turn (`store: false`) stays servable through its thread pin alone; pinned and anchored dispatches do not re-write the pin but slide it through a background touch at most once per hour, never on the request path. A pin commit that is not `written` MUST end the lifecycle as the pin-write requirement states -- the synthesized `response.created` + `response.failed` pair with `error.code` `subscription_overflow_pin_unavailable` on a stream (the row records `subscription_overflow_pin_unverified` for an `unknown` outcome), HTTP `503` with the same code for a non-streaming request -- release the reservation, count `pin_commit_failed` or `pin_commit_unverified`, and mark the thread key so fresh overflow declines it for 60 s (`pin_commit_recent_failure`) instead of paying for a second dispatch or serving a turn a still-settling write could contradict. After a pin's purge a returning conversation is treated as new (documented residual). Neither kill switch -- clearing the designation, or disabling or deleting the source -- MAY send a pinned conversation whose transcript is not proven source-free to a subscription account.

#### Scenario: The second turn follows the pin across sessions, keys and pool recovery

- **GIVEN** thread `t1` received content from the designated source on its first turn
- **AND** the subscription pool has usage headroom again
- **WHEN** the next turn of `t1` arrives with a new `session-id` through a rotated, unscoped API key that allows the model
- **THEN** it is dispatched to the pinned source and no subscription account is selected
- **AND** the request-log row has `source` `subscription_overflow_pinned` and `dispatched_pinned` is counted
- **AND** the pin's `last_seen_at` is slid by a background touch, not by a request-path write

#### Scenario: Pins are written at the content trigger only

- **GIVEN** a fresh dispatch on a thread-keyed request
- **WHEN** the source has emitted only `response.created` and `response.in_progress`
- **THEN** no pin row exists yet
- **WHEN** the first content-bearing frame arrives
- **THEN** the thread pin (and the anchor when `store` is not `false`) is verified durable before that frame reaches the client
- **WHEN** the source's first content-bearing frame is `response.failed`
- **THEN** no pin is written and the attempt is recorded as an error
- **WHEN** the source's first content-bearing frame arrives before any `response.id` and the client did not send `store: false`
- **THEN** no row is written at all and the client receives only the synthesized `response.created` + `response.failed` pair

#### Scenario: Model switch on a pinned conversation

- **WHEN** a turn on a pinned conversation requests another model that is enabled on the pinned source and allowed for the key
- **THEN** it is dispatched to the pinned source
- **WHEN** it requests a model the pinned source does not serve and the transcript carries source reasoning
- **THEN** the response is HTTP `400` `subscription_overflow_source_unavailable` naming the models the source can serve, and the pin is kept

#### Scenario: Neutral release when the source is disabled

- **GIVEN** a pinned conversation whose transcript has no `reasoning` or `compaction` item and no response-owned ids
- **AND** the operator disabled the pinned source
- **WHEN** the next turn arrives
- **THEN** the pin is deleted durably before the request is served, `subscription_overflow_pinned_released_neutral` is logged, the input items lose their top-level `id`, a subscription account serves the turn and `pinned_released_neutral` is counted
- **WHEN** the same turn carries a `reasoning` item with `encrypted_content`
- **THEN** the response is HTTP `400` `subscription_overflow_source_unavailable` and the pin is kept
- **WHEN** the durable delete does not report `written`
- **THEN** the response is HTTP `503` `model_source_unavailable` and no subscription account serves the turn

#### Scenario: Tombstoned conversation

- **GIVEN** a thread pin whose idle limit passed less than 21 days ago
- **WHEN** a turn carrying source ciphertext arrives
- **THEN** the response is HTTP `400` `subscription_overflow_unsupported_input` ("expired") and `pinned_unservable_tombstone` is counted
- **WHEN** a turn whose transcript is source-free arrives
- **THEN** the tombstone is deleted durably and a subscription account serves it

#### Scenario: Transient obstacles fail fast, never to the pool

- **GIVEN** the pinned source's breaker is `open`
- **WHEN** a turn on the pinned conversation arrives
- **THEN** the response is HTTP `503` `model_source_unavailable` with `Retry-After: 2`, never `429`, and no subscription account is selected
- **GIVEN** the pinned source has `max_concurrency` 1 and one dispatch in flight
- **WHEN** a second pinned turn arrives
- **THEN** the response is HTTP `503` `model_source_busy` with `Retry-After: 1`

#### Scenario: Anchored SDK follow-ups stay on the source

- **GIVEN** a `/v1/responses` overflow dispatch with `store` unset completed with source `response.id` `resp_src_1`
- **WHEN** a follow-up carrying `previous_response_id: "resp_src_1"` arrives from the same API key, before or after the designation was cleared
- **THEN** it is dispatched to the same source as an `anchor` dispatch with `source` `subscription_overflow_pinned`
- **WHEN** a follow-up carries an unknown `previous_response_id`
- **THEN** the unchanged owner fail-closed path answers and no source is contacted
- **WHEN** a dispatch is sent with `store: false`
- **THEN** no anchor row is written
- **WHEN** the source's id first appears in a frame after its first content frame
- **THEN** the turn is refused with `subscription_overflow_pin_unavailable` and no anchor is written, so no follow-up can resolve to an unanchored turn

#### Scenario: Unsupported input on a live pinned conversation

- **WHEN** a turn on a pinned conversation ends with a `compaction_trigger` item, references an uploaded file, or carries an `input_image` while the source model lacks vision
- **THEN** the response is HTTP `400` `subscription_overflow_unsupported_input` and the pin is kept
- **AND** the source receives no request, nothing is claimed and `pinned_unsupported_input` is counted
- **WHEN** the source model declares `supports_vision` and the same turn carrying the `input_image` arrives
- **THEN** it is dispatched to the pinned source

#### Scenario: Pin commit failure ends the lifecycle without a second paid dispatch

- **GIVEN** the SQLite writer section is held past the 10 s acquisition deadline
- **WHEN** a fresh dispatch reaches its content trigger
- **THEN** the client receives exactly one synthesized `response.created` + `response.failed` pair with `error.code` `subscription_overflow_pin_unavailable`, the reservation is released, the source stream is closed and `pin_commit_failed` is counted
- **AND** a retry of the same thread within 60 s is answered today's `429` and the source receives no second request
- **WHEN** the same failure happens on a non-streaming request
- **THEN** the response is HTTP `503` `subscription_overflow_pin_unavailable`

#### Scenario: Clearing the designation drains pinned conversations only

- **GIVEN** a pinned conversation and an operator who set the control to Off
- **WHEN** the next turn of that conversation arrives
- **THEN** it is dispatched to the pinned source with its pin's expiry capped by the drain deadline
- **WHEN** a fresh request arrives on an exhausted pool
- **THEN** it is counted `declined_drain_mode` and answered today's `429`

### Requirement: Per-source breaker and admission claims for overflow dispatch

The proxy SHALL keep an in-process, per-source-id failure breaker for subscription-overflow dispatches with the states `closed`, `open` and `half_open`, exported as `codex_lb_model_source_breaker_state{source_id}` (`0` closed, `1` open, `2` half-open) and logged at WARN on every transition as `model_source_breaker state=<state> source_id=<id>`. Counted failures MUST be: before the body, a transport failure, a connect, header or first-frame deadline, a status of `500` or above, or `429`; before the first output item, a failure terminal (`response.failed` or `error`), a transport drop, an idle timeout, or a client abandonment with no frame at all after at least 10 s (`source_stall_abandoned`); after the first frame, an idle timeout or a transport drop -- an exception out of the started body, or a clean end of the stream without a terminal (`model_source_stream_truncated`), whether or not an output item was already yielded. The breaker MUST NOT count `400`, `401`, `403`, `404` or `422`, a client cancel after any frame (`response.created` and `response.in_progress` are frames), a pin-commit failure, an estimate settlement or any decline. Three consecutive counted failures MUST open the breaker for 30 s: fresh overflow declines `breaker_open` (today's `429`) and pinned or anchored requests fail fast with HTTP `503` `model_source_unavailable`. After the open window the breaker is `half_open` and admits exactly one leased trial (lease 120 s): the trial token is claimed together with the concurrency slot as the decision's last await-free step -- by whichever fresh, pinned or anchored request claims first -- and settled through the dispatch owner exactly once: the first output item yielded, or a non-streaming `2xx` with usage, closes the breaker -- at the item, while the trial's stream is still running, so other overflow requests are admitted while it streams; the concurrency slot stays with the dispatch owner until its terminal latch, and a counted failure later in that stream counts toward the threshold in the `closed` state like any other dispatch -- a counted failure before the first item opens it for another 30 s; a client abandonment before the first item without stall evidence, a pin failure or a release without a dispatch (a decline, a key-limit failure, a cancellation, a pre-dispatch error) is inconclusive, releases the trial and makes the next request the trial. An expired lease re-admits a trial. A token MUST be issued in the `closed` state as well, so counted failures are recorded toward the threshold (a breaker that issues no token while closed never opens); no token is issued while `open` or while the half-open lease is held. Every claim -- the slot and the token -- MUST be released by exactly one latch: the decision when it exits without dispatching, the route helper while no owner exists, the dispatch owner afterwards. The per-source concurrency slot enforces the source's `max_concurrency` (`null` unlimited) for overflow exactly as for direct routing, claimed at the decision after the portability walk: a fresh request that finds the source saturated declines `source_busy` and receives today's `429` (no reservation exists yet); a pinned or anchored request receives HTTP `503` `model_source_busy` with `Retry-After: 1`. The breaker observes overflow dispatches only: direct source routing claims its slot without a breaker token and neither counts toward nor is gated by the breaker -- a deviation from the design's shared treatment, accepted so that direct routing's admission is unchanged by this change. Breaker state is per replica; a stalled source costs at most three attempts per replica before it opens.

#### Scenario: Three counted failures open the breaker

- **GIVEN** the designated source answers HTTP `503` before any body
- **WHEN** three fresh dispatches fail in a row
- **THEN** the breaker is `open`, `codex_lb_model_source_breaker_state` for the source is `1` and a `model_source_breaker state=open` line is logged
- **AND** the next fresh request on the exhausted pool is answered today's `429` and counted `declined_breaker_open`
- **AND** a pinned turn is answered HTTP `503` `model_source_unavailable`
- **WHEN** 30 s pass
- **THEN** the breaker is `half_open` and the gauge is `2`

#### Scenario: Uncounted outcomes never open the breaker

- **WHEN** the source answers `401`, `404` or `400` three times, a client cancels after `response.created` three times, or three pin commits fail
- **THEN** the breaker stays `closed`
- **WHEN** the source ends three streams with `response.failed` after their first output item
- **THEN** the breaker stays `closed`

#### Scenario: A stall after the first output item is counted

- **WHEN** three dispatches in a row yield an output item and then hit the idle deadline, drop the transport or end without a terminal
- **THEN** each is a counted failure and the breaker is `open` after the third

#### Scenario: One leased trial while half-open

- **GIVEN** the breaker is `half_open`
- **WHEN** two portable fresh requests arrive together on the exhausted pool
- **THEN** exactly one is dispatched as the trial and the other is counted `declined_breaker_open`
- **WHEN** the trial yields its first output item
- **THEN** the breaker is `closed` while the trial's stream is still running, its slot is still held and the next fresh request is dispatched with a closed-state token
- **WHEN** that trial's stream later stalls into an idle timeout
- **THEN** the breaker stays `closed` with one counted failure toward the next trip
- **WHEN** instead the trial fails with a counted failure before its first item
- **THEN** the breaker is `open` again for 30 s

#### Scenario: A trial released without a dispatch is offered to the next request

- **GIVEN** the breaker is `half_open` and a request has claimed the trial
- **WHEN** that request is declined by the portability verdict, or its API-key limit check raises, or it is cancelled before the owner exists
- **THEN** the trial is released and the next request becomes the trial
- **WHEN** a claimed trial is never settled for 120 s
- **THEN** the lease expires and the next request may claim the trial

#### Scenario: Saturated source

- **GIVEN** the designated source has `max_concurrency` 1 and one fresh dispatch in flight
- **WHEN** a second fresh request arrives on the exhausted pool
- **THEN** it is answered today's `429` `usage_limit_reached` with `resets_at`, counted `declined_source_busy`, and no reservation is created
- **WHEN** a pinned turn arrives instead
- **THEN** it is answered HTTP `503` `model_source_busy` with `Retry-After: 1`

#### Scenario: Direct routing is neither counted nor gated

- **GIVEN** the breaker for source `S` is `open`
- **WHEN** an API key scoped to `S` routes a request directly to `S`
- **THEN** the request is dispatched and its outcome does not change the breaker

### Requirement: Overflow dispatches are attributed to the source, never to an account

Every subscription-overflow dispatch SHALL be logged by its dispatch owner as exactly one request-log row per attempt: `source` `subscription_overflow` for a fresh dispatch and `subscription_overflow_pinned` for a pinned or anchored one, `account_id` null, `model_source_id` and `model_source_kind` set, `api_key_id` when a key authenticated the request, `transport` `http`, `upstream_transport` `openai_compatible_http`, `conversation_id` the client's `thread-id`, `session_id` from the client's session headers, `request_id` the source's `response.id` when observed (the proxy request id otherwise), `archive_request_id` the proxy request id, `requested_service_tier` the tier the client requested (stripped from the forwarded body), `service_tier` null, `status` `success`, `error` or `cancelled` with a stage-naming `error_code`, and usage and cost (source pricing; unpriced yields `0.0`) only when the source reported usage. An estimate settled on a limited key MUST NOT be written as row usage; it is visible only through the estimate counter and its WARN line. No `usage_limit_reached` row MAY be written for a served overflow, and a declined request keeps today's `429` row unchanged. A denied WebSocket handshake writes no row; an in-band WebSocket bounce writes today's connect-failure row. Every finished overflow dispatch MUST record one `codex_lb_upstream_transport_decisions_total{policy="subscription_overflow"}` increment with `sticky` `true` for pinned and anchored dispatches and `false` for fresh ones, and one `codex_lb_model_source_dispatch_total{kind,status}` increment with `kind` `fresh`, `pinned` or `anchor`. Spend is visible through the rows -- the dashboard sums `cost_usd` over the closed set `source IN ('subscription_overflow', 'subscription_overflow_pinned')`, an equality set the `(source, requested_at)` index serves on both dialects, unlike a prefix `LIKE` under a non-C PostgreSQL collation, and the request-log listing exposes the same two values as a `source` filter -- and this version imposes no spend ceiling.

#### Scenario: Fresh dispatch row

- **WHEN** a fresh overflow stream for a request with `service_tier: "priority"` and `thread-id` `t1` completes with `response.completed` carrying `id` `resp_src_1` and usage
- **THEN** exactly one request-log row exists with `source` `subscription_overflow`, `account_id` null, `model_source_id` set, `conversation_id` `t1`, `request_id` `resp_src_1`, `archive_request_id` the proxy request id, `requested_service_tier` `priority`, `service_tier` null and the source's usage and cost
- **AND** `codex_lb_model_source_dispatch_total{kind="fresh",status="success"}` and `codex_lb_upstream_transport_decisions_total{policy="subscription_overflow",sticky="false",status="success"}` are incremented

#### Scenario: Pinned and anchored dispatch rows

- **WHEN** a pinned or anchored dispatch finishes
- **THEN** its row has `source` `subscription_overflow_pinned` and the transport decision is recorded with `sticky` `true`

#### Scenario: Estimates are visible but never written as usage

- **GIVEN** a limited API key and a source that omits `usage`
- **WHEN** an overflow stream completes
- **THEN** the reservation is settled at the estimate, the row's usage columns stay null and `codex_lb_model_source_usage_estimated_total` is incremented

#### Scenario: Declines leave today's rows untouched

- **WHEN** a request is declined by the overflow decision
- **THEN** the request-log row is today's `429` `usage_limit_reached` row and no overflow row exists

### Requirement: Source-direction request headers are constructed, never forwarded

Every HTTP request the proxy sends to an OpenAI-compatible model source -- direct source routing and every subscription-overflow dispatch alike -- SHALL carry a header set the proxy constructs from the source configuration alone: `Accept` (`text/event-stream` for streams, `application/json` otherwise, `*/*` where the endpoint requires it), `Content-Type: application/json` when a body is sent and `Authorization: Bearer <source credential>` when the source has one, plus the HTTP client's own transport headers (`Host`, `Content-Length`, `Accept-Encoding`, `User-Agent`). No header of the inbound client request MAY be forwarded to a source: in particular the client's `authorization`, `chatgpt-account-id`, `session-id`, `thread-id`, every `x-codex-*` header (turn state, window id, turn metadata, parent thread id, installation id), `x-openai-subagent`, `x-openai-memgen-request`, `x-client-request-id`, `x-oai-attestation`, `originator` and `user-agent` MUST never reach a source. This is structural -- the source transport builds its headers and has no access to the inbound request -- and MUST be kept so: the transport module MUST NOT accept inbound headers, and a header-capture test on the overflow path and on the direct path MUST assert the exact constructed set. Direct routing is unchanged by the overflow feature: same builder, same set.

#### Scenario: Overflow dispatch carries only constructed headers

- **GIVEN** a native Codex request carrying `authorization`, `chatgpt-account-id`, `session-id`, `thread-id`, `x-codex-turn-state`, `x-codex-window-id`, `x-openai-subagent: review`, `x-client-request-id`, `x-oai-attestation` and `originator`
- **WHEN** it is dispatched to the designated source
- **THEN** the source receives exactly `host`, `accept`, `content-type`, `content-length`, `authorization`, `user-agent` and `accept-encoding`
- **AND** `authorization` carries the source's credential, not the client's
- **AND** none of the client's identity or telemetry headers is present

#### Scenario: Direct routing sends the same set

- **WHEN** an API key scoped to a source routes the same request directly
- **THEN** the source receives the same header set

### Requirement: Ship-dark rollout and canary

Subscription-exhaustion overflow SHALL ship dark: the feature is inactive until an operator stores a designation, the settings fast path guarantees zero request-path cost until then, and there is no environment variable, per-replica flag or feature toggle other than the fleet-wide `subscription_overflow_source_id` row. A production designation MUST NOT be stored before a release that contains this change with anchors and the WebSocket parity wired is deployed everywhere: earlier releases would pin conversations over HTTP that a WebSocket session could then resume on a subscription account, and SDK follow-ups anchored on a source `response.id` would fail closed. Because the designation is fleet-wide, the canary MUST be a separate deployment with its own database, designating a low-limit priced source that serves a standard-Responses registry model (never a Responses-Lite one). Before the production flip the canary MUST exercise every drill and observe the stated outcome: **disconnect** (the client leaves during time-to-first-byte: one `cancelled` row, no pin, slot and reservation released); **stall** (a black-holed source: `504` `model_source_timeout` within 60 s before any `200`, the breaker `open` within three attempts, ChatGPT traffic and the ChatGPT connector unaffected); **silent headers** (`200` then nothing: `504` `model_source_timeout` at 30 s and nothing sent to the client); **neutral release** (disable the source while a ciphertext-free conversation is pinned: a subscription account serves it and the pin is gone; a reasoning-bearing one: `400` `subscription_overflow_source_unavailable`); **clear-then-touch** (clear the designation and keep using a pinned conversation daily: the source serves it until day 7, never a subscription account, and the pin table is empty at `drain_until`); **both kill switches** (Off: fresh overflow stops within the settings-cache window and pinned conversations drain; disable or delete the source: release or `400`). The canary MUST also watch `codex_lb_subscription_overflow_total`, the request-log rows and the cost figures over one usage-reset cycle, confirm that a pinned conversation survives the pool's reset, `codex resume` and an API-key rotation and then tombstones, and profile with py-spy only at 10-20 Hz in short bursts. The WebSocket downgrade relies on Codex `0.99.0` or newer (the handshake `426` to HTTP fallback); the operator documentation MUST state this.

#### Scenario: The production flip is gated on the wired release

- **GIVEN** a production deployment that does not yet run the release containing wired anchors and WebSocket parity
- **WHEN** an operator plans to designate a source
- **THEN** the designation stays unset until that release is deployed everywhere

#### Scenario: A canary needs its own database

- **WHEN** a canary designation is stored in a database shared with production
- **THEN** every production replica reads the same designation within the settings-cache window, because it is a fleet-wide row and there is no per-replica flag
- **AND** therefore the canary runs against its own database

#### Scenario: Drill outcomes

- **GIVEN** a canary deployment with a designated low-limit priced source
- **WHEN** the client disconnects during time-to-first-byte
- **THEN** one `cancelled` row is written, no pin exists and the slot and reservation are released
- **WHEN** the source is black-holed
- **THEN** the request fails with `504` `model_source_timeout` within 60 s, the breaker opens within three attempts and ChatGPT traffic is unaffected
- **WHEN** the source answers `200` and then nothing
- **THEN** the request fails with `504` `model_source_timeout` at 30 s and nothing was sent to the client
- **WHEN** the source is disabled while a ciphertext-free conversation is pinned
- **THEN** a subscription account serves the next turn and the pin is gone, while a reasoning-bearing conversation receives `400` `subscription_overflow_source_unavailable`
- **WHEN** the designation is cleared and a pinned conversation is used daily
- **THEN** the source serves it until day 7 and never a subscription account, and the pin table is empty at `drain_until`
- **WHEN** the control is set to Off, or the source is disabled or deleted
- **THEN** fresh overflow stops within the settings-cache window and pinned conversations drain, are released or receive `400`
