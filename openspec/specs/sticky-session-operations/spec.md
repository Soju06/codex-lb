# sticky-session-operations Specification

## Purpose

Define sticky-session operation contracts so durable sessions, dashboard affinity, and prompt-cache affinity stay distinct.
## Requirements
### Requirement: Sticky sessions are explicitly typed

The system SHALL persist each sticky-session mapping with an explicit kind so durable Codex backend affinity, durable dashboard sticky-thread routing, and bounded prompt-cache affinity can be managed independently. Budget-pressure reallocation MUST apply only to mappings whose kind/source is soft. A raw or legacy `codex_session` mapping MUST remain owner-bound because it may represent explicit turn-state continuity; budget pressure MUST NOT delete or rebind it.

An explicit Codex goal-continuation restart MAY abandon a raw legacy `codex_session` owner only when the complete Responses payload is account-neutral and self-contained: it MUST have no nonblank `previous_response_id`, no nonblank `conversation`, no account-scoped input file or image reference, and no unresolved or orphan tool state. Classification MUST use the canonical upstream request form so accepted compatibility controls and transport-envelope fields do not make equivalent requests disagree. The owner MUST be persisted as `PAUSED`, `RATE_LIMITED`, or `QUOTA_EXCEEDED` and MUST belong to the authenticated request's account-assignment and security-policy scope computed before model and service-tier eligibility; local capacity, model eligibility, retry exclusions, runtime health, budget pressure, and an out-of-scope owner MUST NOT determine mutation authority. The retirement write MUST compare the current mapping owner and unavailable account status atomically, MUST preserve a concurrently changed mapping or recovered owner, and on success MUST let normal selection establish affinity to the replacement account. Because a raw key's persisted source is ambiguous, goal-restart abandonment MUST apply only to `session_header` interpretation and MUST retain the stored account as hard ownership for an explicit `turn_state` lookup using the same text. During a rolling deployment or rollback, replicas that do not understand source-qualified abandonment MUST continue treating that retained account as hard ownership. A selector that observes source-qualified abandonment initially or after losing the retirement compare-and-set MUST exclude the retained retired owner until replacement affinity is persisted, even if its account inputs predate retirement. Restart authority MUST remain scoped to the classified request and MUST NOT persist on a reusable bridge for later requests. A live or durable HTTP bridge for the same process session MUST NOT bypass this guarded selection through local reuse, owner forwarding, or preferred-owner promotion. Canonical replacement MUST preserve an already reserved predecessor request's authority to submit on its detached draining generation after queue publication clears the mutable reservation marker. A detached predecessor MAY finish its admitted response but MUST NOT publish new turn-state or previous-response aliases under the replacement generation's canonical key. Every detached generation, including an idle generation already marked closed for admission, MUST remain owned by the bridge lifecycle, MUST count against the configured session cap until resource closure completes, and MUST be closed during service shutdown, account invalidation, or drained reservation cleanup. The admission-only closed state MUST NOT be treated as proof that the socket and leases have a close owner. Resource teardown MUST be single-flight, and all close paths MUST release detached-generation ownership only after resource closure finishes, even when a close caller is cancelled. Shutdown MUST schedule and await every snapshotted generation before propagating cancellation. A new local bridge generation that replaces durable ownership under the same replica identity MUST advance the durable owner epoch before serving requests so a predecessor's late release cannot close the replacement lease, including when model-transition isolation discards the durable lookup as a routing input.

A later security-authorized bridge replacement that revalidates a raw legacy row MUST preserve the request's typed continuity source. A source-scoped session-header abandonment MUST remain ownerless for that replacement while an explicit turn-state lookup of the same raw value remains owner-bound. A planned capacity eviction MUST NOT stop counting a detached generation merely because its bounded close wait timed out. Shutdown MUST retain any generation whose resource close fails so a later shutdown pass can retry finalization. Drain status MUST count pending or queued work on a detached generation even after it is closed for admission. When a verified restart replaces an idle predecessor that fills the configured session cap, admission MUST give that predecessor synchronous bounded-close ownership and MUST recheck actual lifecycle capacity before opening the replacement.

Outside the explicit goal-continuation restart abandonment defined above, request-time selection MUST NOT reallocate a hard `codex_session` mapping to a different account, including when its owner is unavailable. Independently of request-time selection, a periodic background job MAY retire (never rebind) a hard `codex_session` mapping once its owner has been durably unavailable — not merely transiently rate-limited or paused — for well past its own recovery point, so a future request against that session simply re-resolves fresh instead of failing closed forever. Retiring MUST happen in two phases, never a single delete: the mapping is first tombstoned (marked deliberately abandoned, not deleted), and only dropped outright after it has sat unclaimed for a further grace window. A tombstoned mapping MUST be exempt from the ambiguous-conversation-owner check, so a `conversation`-continuity request against it can select a fresh owner instead of failing closed forever with no way to recover. When the owner's own `reset_at` is known and still in the future, that recovery point MUST take priority over any flat cutoff. An account that is already unavailable when the process starts MUST still receive the full grace window from that point forward, so a merely-transient outage that predates a deploy is never purged on the first cleanup cycle after it; that seeding MUST happen at most once per database, not once per process start, so a fast-redeploying multi-replica deployment cannot perpetually reset the grace clock for a durably-dead mapping.

#### Scenario: Soft sticky reallocation uses split primary and secondary pressure thresholds
- **WHEN** a request resolves an existing prompt-cache, sticky-thread, or other explicitly soft mapping
- **AND** the pinned account is otherwise eligible to serve traffic
- **AND** the pinned account is strictly above either the configured primary sticky reallocation threshold or the configured secondary sticky reallocation threshold
- **AND** another eligible account remains at or below both configured sticky reallocation thresholds
- **THEN** selection rebinds the sticky-session mapping to the healthier account before sending the request upstream

#### Scenario: Sticky reallocation preserves a pinned account when every candidate is split-threshold pressured
- **WHEN** a request resolves an existing soft sticky-session mapping
- **AND** the pinned account is otherwise eligible to serve traffic
- **AND** the pinned account is strictly above either configured sticky reallocation threshold
- **AND** every other eligible account is also strictly above at least one configured sticky reallocation threshold
- **THEN** selection retains the existing pinned account to avoid sticky-pin thrashing

#### Scenario: Fresh selection does not apply sticky secondary pressure threshold
- **WHEN** a request has no sticky-session mapping
- **AND** one eligible account is above the configured secondary sticky reallocation threshold but below the normal primary budget threshold
- **THEN** the account remains eligible for ordinary non-sticky routing according to the selected routing strategy

#### Scenario: Hard Codex mapping ignores budget-pressure reallocation
- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A is above a sticky budget-pressure threshold
- **AND** account B has more remaining budget
- **WHEN** the request is selected
- **THEN** selection remains constrained to account A
- **AND** the raw mapping is neither deleted nor rebound to account B

#### Scenario: Unavailable hard Codex owner does not lose its mapping
- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A is temporarily quota-exceeded or otherwise unusable
- **AND** account B is healthy
- **WHEN** an ordinary request or an unsafe restart-shaped request requires the mapping
- **THEN** the request fails closed instead of selecting account B
- **AND** the raw mapping is neither deleted nor rebound

#### Scenario: Self-contained goal restart abandons unavailable legacy owner
- **GIVEN** a process-session identifier has a raw legacy `codex_session` mapping to account A
- **AND** account A is paused, rate-limited, or quota-exceeded
- **AND** account B is eligible
- **WHEN** Codex sends the recognized goal-continuation marker with an account-neutral self-contained full resend and no other continuity dependency
- **THEN** the proxy marks the still-current raw mapping to account A abandoned only for process-session interpretation
- **AND** it routes the restarted turn to account B
- **AND** subsequent session or response continuity remains on account B

#### Scenario: Goal restart cannot erase colliding explicit turn-state ownership
- **GIVEN** a raw legacy `codex_session` row was written as explicit turn-state ownership for account A
- **AND** a process-session header later uses the same client-controlled text
- **WHEN** a marked self-contained goal restart abandons that text for process-session interpretation
- **THEN** the process-session restart may select account B
- **AND** an explicit turn-state lookup of the same text remains hard-bound to account A

#### Scenario: Goal restart with process session and thread-id abandons the unavailable raw owner
- **GIVEN** a process-session identifier has a raw legacy `codex_session` mapping to account A
- **AND** account A is paused, rate-limited, or quota-exceeded
- **AND** account B is eligible
- **AND** the request also carries a distinct `thread-id`
- **WHEN** Codex sends the recognized goal-continuation marker with an account-neutral self-contained full resend and no other continuity dependency
- **THEN** the proxy marks the still-current raw mapping to account A abandoned only for process-session interpretation
- **AND** it routes the restarted turn to account B
- **AND** subsequent same-thread continuity remains on account B

#### Scenario: Thread-id on a goal restart cannot erase colliding explicit turn-state ownership
- **GIVEN** a raw legacy `codex_session` row was written as explicit turn-state ownership for account A
- **AND** a later request carries the same text as a process-session header plus a distinct `thread-id`
- **WHEN** a marked self-contained goal restart abandons that text for process-session interpretation
- **THEN** the restart may select account B
- **AND** an explicit turn-state lookup of the same text remains hard-bound to account A

#### Scenario: Account-dependent thread-scoped restart stays fail-closed
- **GIVEN** a process-session identifier has a raw legacy mapping to unavailable account A
- **AND** the request carries a distinct `thread-id`
- **AND** the body has a previous response, conversation, file pin, or unresolved tool state
- **WHEN** the request is selected
- **THEN** the request fails closed on account A
- **AND** the raw mapping is neither deleted nor rebound

#### Scenario: Source-qualified retirement fails closed on an older replica
- **GIVEN** a current replica marks a raw account A mapping abandoned only for `session_header` interpretation
- **WHEN** a replica that does not understand abandonment scope reads the same raw mapping
- **THEN** it continues to resolve account A as hard ownership
- **AND** it cannot re-pin a colliding explicit turn state to another account

#### Scenario: Model eligibility does not narrow retirement authority
- **GIVEN** unavailable account A is inside the authenticated account-assignment and security-policy scope
- **AND** account A cannot serve the restart's requested model while account B can
- **WHEN** a marked self-contained goal restart evaluates the raw mapping owned by account A
- **THEN** account A remains authorized for the guarded abandonment mutation
- **AND** model and service-tier eligibility apply only when selecting the replacement

#### Scenario: Equivalent request forms receive the same restart classification
- **GIVEN** two marked self-contained goal restarts differ only by accepted compatibility controls or a transport-only response-create envelope
- **WHEN** the proxy classifies their account-neutral replay safety
- **THEN** it evaluates the same canonical upstream request fields for both forms
- **AND** neither form remains pinned merely because its accepted input representation differs

#### Scenario: Goal restart bypasses a stale live HTTP bridge owner
- **GIVEN** a live HTTP bridge and raw legacy mapping both identify account A for a process session
- **AND** the bridge's detached account snapshot still reports account A active
- **AND** account A is now persisted as paused, rate-limited, or quota-exceeded
- **WHEN** a marked account-neutral self-contained goal restart arrives for that process session
- **THEN** the proxy does not reuse or forward to account A's bridge
- **AND** guarded selection retires the raw owner before a replacement bridge is created on eligible account B

#### Scenario: Restart authority does not outlive its request
- **GIVEN** a marked self-contained goal restart creates a reusable HTTP bridge while legacy owner account A is healthy
- **WHEN** a later ordinary request reuses that bridge and account A has become unavailable
- **THEN** the ordinary request fails closed instead of inheriting the earlier restart's retirement authority
- **AND** the raw mapping to account A is neither tombstoned nor rebound

#### Scenario: Reserved predecessor submits after canonical replacement
- **GIVEN** an unanchored request has reserved the canonical session-header bridge before submit
- **AND** a verified goal restart replaces that canonical bridge while the reserved request is preparing its payload
- **WHEN** the reserved request publishes queued activity and clears its mutable reservation marker
- **THEN** the request submits exactly once on its detached predecessor generation
- **AND** canonical replacement does not reject that request as unregistered or replaced

#### Scenario: Detached restart generations remain capacity bounded
- **GIVEN** repeated verified restarts replace canonical bridges that still own visible or reserved requests
- **WHEN** the number of canonical, detached-live, and in-flight generations reaches the configured session cap
- **THEN** the service refuses another generation with its bounded local-capacity error
- **AND** detached sockets, readers, durable leases, and account leases are not omitted from capacity accounting

#### Scenario: Idle detached predecessor remains capacity owned while closing
- **GIVEN** a verified restart replaces an idle canonical bridge and its resource close is still running
- **WHEN** another restart would exceed the configured session cap
- **THEN** the admission-closed predecessor still counts as a detached generation
- **AND** the service either closes an evictable canonical generation before replacement creation or refuses the new generation

#### Scenario: Closed detached request settlement blocks restart
- **GIVEN** canonical replacement marked a detached predecessor closed for admission
- **AND** that predecessor still has pending or queued request settlement
- **WHEN** the service reports HTTP bridge drain status
- **THEN** the bridge remains active and restart-blocking
- **AND** it stops blocking only after the unsettled work reaches zero

#### Scenario: One-session restart closes its idle predecessor before cap enforcement
- **GIVEN** the bridge session cap is one and an idle canonical predecessor occupies that generation
- **WHEN** a verified goal restart forces canonical replacement
- **THEN** admission detaches the predecessor and gives it synchronous bounded-close ownership
- **AND** it opens the replacement only after close finalization releases the slot, otherwise it returns the bounded capacity refusal

#### Scenario: Timed-out LRU close does not manufacture capacity
- **GIVEN** admission detaches an idle LRU generation and reserves an in-flight replacement slot
- **AND** the bounded close wait returns before that generation's resource finalizer completes
- **WHEN** admission rechecks the configured session cap before opening the replacement socket
- **THEN** the detached generation still consumes capacity
- **AND** the service refuses replacement creation rather than exceeding the cap

#### Scenario: Shutdown closes detached bridge generations
- **GIVEN** canonical replacement detached an older generation whose request is still draining
- **WHEN** the service closes all HTTP bridge sessions
- **THEN** it closes both canonical and detached generations
- **AND** no detached socket, reader, durable lease, or account lease escapes shutdown ownership

#### Scenario: Shutdown cancellation does not orphan later generations
- **GIVEN** shutdown snapshots multiple canonical or detached bridge generations
- **AND** one generation has a slow resource close
- **WHEN** the shutdown caller is cancelled
- **THEN** every snapshotted generation receives a close owner before cancellation is propagated
- **AND** shutdown awaits all of those closes through resource finalization

#### Scenario: Failed shutdown close remains retryable
- **GIVEN** shutdown removes a canonical generation from routing and starts its resource close
- **WHEN** pending settlement or another resource finalizer fails
- **THEN** the generation remains in detached lifecycle ownership
- **AND** a later shutdown pass retries its close instead of losing the socket or leases

#### Scenario: Detached predecessor cannot publish replacement continuity
- **GIVEN** canonical replacement detaches an older generation while its admitted response is still draining
- **WHEN** that predecessor receives a new turn-state or previous-response alias
- **THEN** it does not publish the alias under the canonical key now occupied by the replacement
- **AND** the predecessor may still finish delivering its already admitted response

#### Scenario: Detached generation closes after its final reservation ends
- **GIVEN** a detached predecessor is retained only by an unsubmitted request reservation
- **WHEN** request finalization releases that reservation without submitting
- **THEN** the service closes the drained predecessor and releases its capacity ownership after resource closure finishes

#### Scenario: Account invalidation includes detached generations
- **GIVEN** an account owns both canonical and detached bridge generations
- **WHEN** the account is deactivated, requires reauthentication, or changes proxy binding
- **THEN** the service closes every generation authenticated to that account
- **AND** no detached socket remains routed through the invalid account binding

#### Scenario: Admission-closed detached generation is still invalidated
- **GIVEN** a detached generation is marked closed for admission but has no resource-close owner
- **WHEN** its account is invalidated
- **THEN** the service schedules resource teardown for that generation
- **AND** an already owned or successfully finalized close is not scheduled twice

#### Scenario: Same-replica model replacement advances the durable epoch
- **GIVEN** a durable bridge row names the current replica and an older model
- **WHEN** model-transition isolation creates a replacement generation and stops using that row for routing
- **THEN** the replacement claim still advances the durable owner epoch
- **AND** the predecessor's late release cannot close the replacement lease

#### Scenario: Stale selection snapshot cannot repin a retired owner
- **GIVEN** restart selection loaded account A as active before guarded retirement observes its unavailable persisted status
- **WHEN** guarded retirement tombstones account A's still-current raw legacy mapping
- **THEN** the remainder of that selection excludes account A from the stale snapshot
- **AND** the namespaced process-session mapping is not established on account A

#### Scenario: Retirement CAS loser excludes the winner's retired owner
- **GIVEN** two marked restarts read the same raw account A mapping and stale account inputs
- **AND** the first restart marks account A abandoned only for `session_header` interpretation
- **WHEN** the second restart loses its retirement compare-and-set and rereads that marker
- **THEN** the second restart excludes retained account A from its stale inputs
- **AND** it cannot establish replacement affinity on account A

#### Scenario: Goal marker does not override account-scoped continuity
- **GIVEN** a marked goal-continuation request carries a nonblank `previous_response_id`, nonblank `conversation`, account-scoped file or image reference, or unresolved tool output
- **WHEN** its hard owner is unavailable
- **THEN** the request fails closed
- **AND** the hard mapping is not abandoned

#### Scenario: Healthy owner is not abandoned
- **GIVEN** a marked account-neutral goal-continuation restart has a raw legacy owner that is still active
- **WHEN** the owner is locally capped, excluded, budget-pressured, or transiently unhealthy
- **THEN** the mapping remains owner-bound
- **AND** the restart does not retire it as unavailable

#### Scenario: Concurrent owner change wins retirement race
- **GIVEN** restart selection observed a raw legacy mapping to unavailable account A
- **WHEN** another operation rebinds that mapping or restores the owner before the retirement write executes
- **THEN** the compare-and-set retirement does not tombstone the newer state
- **AND** selection preserves fail-closed ownership semantics

#### Scenario: Scoped API key cannot retire another pool's owner
- **GIVEN** a raw legacy `codex_session` mapping points to unavailable account A
- **AND** the authenticated API key's effective account-policy scope contains account B but not account A
- **WHEN** the key sends a marked account-neutral goal-continuation restart for that session
- **THEN** the request fails closed before upstream dispatch
- **AND** the raw mapping to account A is neither tombstoned nor rebound

#### Scenario: A durably unavailable hard Codex owner's mapping is eventually tombstoned
- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A is still `PAUSED`, `RATE_LIMITED`, or `QUOTA_EXCEEDED`
- **AND** the later of the mapping's last use and account A's transition into
  an unavailable status is before a conservative cutoff
- **AND** the mapping has not already been tombstoned
- **WHEN** the periodic sticky-session cleanup job runs
- **THEN** the mapping is tombstoned (marked deliberately abandoned) rather than deleted
- **AND** it is not rebound to any other account
- **AND** the next request against that session resolves a fresh mapping instead of failing closed

#### Scenario: A tombstoned hard Codex mapping unblocks a conversation-continuity request
- **GIVEN** a hard `codex_session` mapping for a turn state has been tombstoned by the periodic cleanup job
- **AND** the request's `conversation` field is non-empty, requiring an unambiguous owner
- **AND** the account pool has more than one eligible account
- **WHEN** the request is selected
- **THEN** selection does not fail closed with the ambiguous-conversation-owner error solely because of the tombstoned mapping
- **AND** selection proceeds to choose a fresh eligible account
- **AND** the resulting mapping write clears the tombstone, restoring a normal hard mapping for that turn state

#### Scenario: An unclaimed tombstone is eventually deleted outright
- **GIVEN** a hard `codex_session` mapping was tombstoned by the periodic cleanup job
- **AND** no request has re-established an owner for it since
- **AND** the tombstone's own abandonment timestamp is before a further conservative cutoff
- **WHEN** the periodic sticky-session cleanup job runs
- **THEN** the mapping is deleted outright
- **AND** a subsequent request against that session falls back to the same fail-closed default as a session that was never seen

#### Scenario: A merely transient hard Codex owner outage is never purged
- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A became rate-limited or paused more recently than the
  conservative cutoff, even if the mapping itself is older
- **WHEN** the periodic sticky-session cleanup job runs
- **THEN** the mapping is left untouched

#### Scenario: A known future reset_at overrides the flat cutoff
- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A is `RATE_LIMITED` or `QUOTA_EXCEEDED` with a known `reset_at` still in the future
- **AND** the mapping's timestamp is already before the conservative cutoff
- **WHEN** the periodic sticky-session cleanup job runs
- **THEN** the mapping is left untouched
- **AND** it remains eligible for purging only once `reset_at` has passed and the mapping is still stale by the cutoff

#### Scenario: An outage that predates process startup still gets its grace window
- **GIVEN** account A is already `PAUSED`, `RATE_LIMITED`, or `QUOTA_EXCEEDED` when this process starts
- **AND** its hard `codex_session` mapping's timestamp already predates the conservative cutoff, unrelated to when the outage actually began
- **AND** this is the first process, ever, to boot against this database since this behavior shipped
- **WHEN** the process completes startup
- **THEN** the mapping's timestamp is refreshed to the startup time
- **AND** the first periodic cleanup cycle after startup does not purge that mapping solely because of its pre-startup timestamp

#### Scenario: Startup seeding runs at most once per database
- **GIVEN** the one-time startup-seeding backfill has already run, on this or any other replica sharing the same database
- **WHEN** a process (the same replica restarting, or a different replica) completes startup
- **THEN** no hard `codex_session` mapping's timestamp is refreshed by this seeding pass
- **AND** an account that has remained unavailable since the original backfill is not given a fresh grace window merely by this process starting

### Requirement: Dashboard exposes sticky-session administration
The system SHALL provide dashboard APIs for listing sticky-session mappings, deleting mappings by explicit `key` and `kind` through the batch delete endpoint, and purging stale mappings. The system SHALL NOT expose a per-item `DELETE /api/sticky-sessions/{kind}/{key}` route; deleting one mapping is a one-entry batch delete.

#### Scenario: List sticky-session mappings
- **WHEN** the dashboard requests sticky-session entries
- **THEN** the response includes each mapping's `key`, `account_id`, `kind`, `created_at`, `updated_at`, `expires_at`, and `is_stale`
- **AND** the response includes the total number of stale `prompt_cache` mappings that currently exist beyond the returned page

#### Scenario: List only stale mappings
- **WHEN** the dashboard requests sticky-session entries with `staleOnly=true`
- **THEN** the system applies stale prompt-cache filtering before enforcing the result limit

#### Scenario: Delete one mapping
- **WHEN** the dashboard posts a batch delete containing exactly one `{key, kind}` entry
- **THEN** the system removes that mapping and reports it in `deleted` with `deletedCount` 1
- **AND** a missing or reserved mapping is reported in `failed` with reason `not_found` instead of a route-level error

#### Scenario: Purge stale prompt-cache mappings
- **WHEN** the dashboard requests a stale purge
- **THEN** the system deletes only stale `prompt_cache` mappings and leaves durable mappings untouched

### Requirement: Prompt-cache mappings are cleaned up proactively
The system SHALL run a background cleanup loop that deletes stale `prompt_cache` mappings using the current dashboard prompt-cache affinity TTL.

#### Scenario: Cleanup loop removes stale prompt-cache mappings
- **WHEN** the cleanup loop runs and finds `prompt_cache` mappings older than the configured TTL
- **THEN** it deletes those mappings

#### Scenario: Cleanup loop preserves durable mappings
- **WHEN** the cleanup loop runs
- **THEN** it does not delete `codex_session` or `sticky_thread` mappings regardless of age

### Requirement: Soft bridge affinity can reroute under local pressure

Prompt-cache and sticky-thread bridge affinity that does not carry a hard continuity dependency MUST be treated as soft. A client-supplied or proxy-derived `prompt_cache_key` is a cache-locality hint, not a correctness dependency; the proxy MAY reroute it under local pressure and accept lower cache-hit rates. When the preferred soft bridge session is saturated by queue depth, response-create gate pressure, bridge capacity, or account-local caps, the service MUST evaluate other eligible accounts/sessions before returning a local overload response. The service MUST emit internal diagnostics such as `internal_soft_affinity_reroute` for successful reroutes without adding those diagnostic names to the stable failure taxonomy.

#### Scenario: Prompt-cache bridge queue reroutes to an eligible account

- **GIVEN** a prompt-cache request's preferred bridge session queue is full
- **AND** another eligible account/session is below cap
- **WHEN** the request has no hard previous-response or turn-state continuity dependency
- **THEN** the proxy routes to the alternate account/session
- **AND** records an internal soft-affinity reroute diagnostic

#### Scenario: Prompt cache key does not override hard previous-response continuity

- **GIVEN** a `/v1/responses` request carries both `previous_response_id` and `prompt_cache_key`
- **AND** the previous response owner is known
- **WHEN** the prompt-cache preferred account differs from the previous-response owner
- **THEN** the proxy treats the request as hard owner-bound to the previous-response owner
- **AND** it does not route to the prompt-cache account when that account cannot preserve the stored response continuation

### Requirement: Hard continuity remains owner-bound and bounded

Requests that depend on `previous_response_id`, hard turn-state, nonblank `conversation`, account-scoped `input_file.file_id` pins, live or durable bridge ownership, replay/reattach state, or another required owner continuity source MUST NOT silently reroute to an account that cannot preserve continuity. A resolved required owner MUST override bare process-session locality and MUST be selected without consulting or rewriting that soft mapping. A `previous_response_id` is a stored-object continuation reference and remains owner-bound even when the same request also carries a session header, `prompt_cache_key`, or another soft locality key. If independently resolved hard sources identify different accounts, if live durable referenced-file pins identify different accounts, or if a request has partial live durable file-pin coverage, the service MUST fail closed before upstream dispatch. A request for which no referenced file has a live durable pin MUST preserve opaque `file_id` compatibility and proceed without inventing ownership evidence. If the owner account/session is unavailable or saturated, the service MUST fail closed with an explicit retryable continuity/local overload reason instead of flooding the owner queue indefinitely.

Every HTTP, compact, direct WebSocket, and HTTP-bridge transport MUST resolve explicit turn state against both live and durable bridge aliases. Live, durable, previous-response, file, and explicit turn-state evidence MUST be compared independently; source ordering MUST NOT choose the first match when distinct sessions or accounts resolve. A reused direct WebSocket MUST repeat nonblank `conversation` ownership validation for each response-create frame because the existing socket account proves only the current route. Single-account routing MUST constrain effective routing without narrowing the ownership-candidate pool used by that validation.

When an HTTP-bridge owner is on another replica, the origin MUST forward its resolved durable file owner in authenticated full-context metadata. The receiving owner MUST perform its own fresh shared-database lookup and MUST require that durable result to match the forwarded owner. A missing or conflicting receiver-side durable owner MUST fail closed before account selection or upstream invocation. A retired direct WebSocket's upstream turn-state token MUST NOT be sent to a different account selected for a later movable bare-session request.

A nonblank `conversation` without a dedicated resolved owner MUST proceed only when an explicit hard Codex mapping proves ownership or exactly one account remains in the model/API-key/security-scoped selection pool before transient additional-quota availability, retry exclusions, runtime health, budget, or account-cap filtering. A temporarily quota-filtered, excluded, unhealthy, or capped candidate MUST remain part of this ambiguity check because it may be the actual owner. A bare process-session mapping MUST NOT prove conversation ownership.

#### Scenario: Previous-response owner queue is saturated

- **WHEN** a `/v1/responses` follow-up requires a previous-response owner
- **AND** the owner session queue or account cap is saturated
- **THEN** the service fails closed with `hard_affinity_saturated`, `previous_response_owner_unavailable`, or the applicable stable `account_stream_cap` / `account_response_create_cap` code
- **AND** it does not route to an unrelated account that lacks continuity state

#### Scenario: File-pinned request owner is capped

- **WHEN** a `/v1/responses` request references an `input_file.file_id` pinned to an owner account
- **AND** the owner account is at its account stream or response-create cap
- **THEN** the service returns a local account-cap overload for the owner
- **AND** it does not route the file reference to another account

#### Scenario: File-pinned request owner overrides process-session locality

- **GIVEN** a request carries a bare process-session header mapped to account A
- **AND** its `input_file.file_id` is durably pinned to account B
- **WHEN** the request is routed
- **THEN** account B is treated as the required owner
- **AND** the process-session mapping is neither consulted as an owner nor rewritten

#### Scenario: File-pinned request owner overrides thread locality

- **GIVEN** a request carries a `thread-id` whose bounded mapping points to account A
- **AND** its `input_file.file_id` is durably pinned to account B
- **WHEN** the request is routed
- **THEN** account B is treated as the required owner
- **AND** the thread mapping is neither consulted as an owner nor rewritten

#### Scenario: Conflicting hard owners fail closed

- **GIVEN** a turn state, previous response, bridge, or input file resolves to account A
- **AND** another hard source on the same request resolves to account B
- **WHEN** the request is routed
- **THEN** the service fails with `continuity_owner_conflict` before upstream dispatch
- **AND** source ordering does not choose either owner

#### Scenario: Partial or cross-account file pins fail closed

- **GIVEN** a request references multiple account-scoped input files
- **AND** at least one file has a live durable owner pin
- **AND** another file has no live durable owner pin or the live pins resolve to different accounts
- **WHEN** the request is routed
- **THEN** the service fails with `file_owner_unavailable` or `continuity_owner_conflict`
- **AND** it does not route the files using a soft affinity account

#### Scenario: Opaque file IDs with no live durable pins preserve compatibility

- **GIVEN** a request references one or more `input_file.file_id` values
- **AND** none of those IDs has a live durable owner pin
- **WHEN** the request is routed
- **THEN** the service forwards the opaque file references under ordinary unpinned routing
- **AND** it does not invent a hard owner or fail solely because durable pin metadata is absent

#### Scenario: Ambiguous conversation fails closed

- **GIVEN** a request carries nonblank `conversation` continuity and only bare process-session affinity
- **AND** more than one account is eligible
- **WHEN** no dedicated or hard-mapping owner can be resolved
- **THEN** the request fails with a stable owner-unavailable error before upstream dispatch

#### Scenario: Account-cap pressure does not manufacture a conversation owner

- **GIVEN** two accounts remain in the model/API-key/security-scoped selection pool
- **AND** one account is temporarily at its local account cap
- **WHEN** a request carries nonblank `conversation` continuity without a dedicated or hard-mapping owner
- **THEN** the request still fails with a stable owner-unavailable error
- **AND** the uncapped account is not treated as the unique owner

#### Scenario: Retry or additional-quota filtering does not manufacture a conversation owner

- **GIVEN** two accounts remain in the model/API-key/security-scoped selection pool
- **AND** retry exclusion or transient additional-quota availability removes one from the effective routing pool
- **WHEN** a request carries nonblank `conversation` continuity without a dedicated or hard-mapping owner
- **THEN** the request still fails with a stable owner-unavailable error
- **AND** the remaining effective account is not treated as the unique owner

#### Scenario: Account status does not manufacture a conversation owner

- **GIVEN** two accounts are in the model/API-key/security ownership pool
- **AND** one account is paused, requires reauthentication, deactivated, or otherwise unavailable for routing
- **WHEN** a request carries nonblank `conversation` continuity without a dedicated or hard-mapping owner
- **THEN** the request still fails with a stable owner-unavailable error
- **AND** the active account is not treated as the unique owner

#### Scenario: Preferred file owner does not manufacture a conversation owner

- **GIVEN** a request carries nonblank `conversation` continuity and a file durably pinned to account B
- **AND** another account remains in the model/API-key/security ownership pool
- **WHEN** no dedicated conversation owner can be resolved
- **THEN** file ownership does not narrow the conversation ambiguity check to account B
- **AND** the request fails closed before upstream dispatch

#### Scenario: Bridge turn state is owner-bound across transports

- **GIVEN** an HTTP bridge registered a turn-state alias for account A
- **WHEN** the alias is reused through compact, plain HTTP streaming, or direct WebSocket transport
- **THEN** each transport treats account A as the required owner
- **AND** it does not fall back to unrelated sticky affinity

#### Scenario: Independent bridge aliases conflict

- **GIVEN** a live or durable turn-state alias resolves to one bridge session
- **AND** a previous-response alias on the same request resolves to a distinct session or account
- **WHEN** the request is routed
- **THEN** the service fails with `continuity_owner_conflict`
- **AND** alias lookup order does not select either session

#### Scenario: Reused WebSocket revalidates conversation ownership

- **GIVEN** a direct upstream WebSocket is already open on account A
- **AND** a later response-create frame carries nonblank `conversation`
- **WHEN** more than one account remains in the ownership-candidate pool
- **THEN** the later frame fails with a stable owner-unavailable error before upstream send
- **AND** the existing socket account is not treated as ownership proof

#### Scenario: Single-account routing does not manufacture conversation ownership

- **GIVEN** single-account routing selects account A
- **AND** multiple accounts remain in the model/API-key/security ownership pool
- **WHEN** a request carries nonblank `conversation` without dedicated owner evidence
- **THEN** the request remains ambiguous and fails closed
- **AND** only the effective routing states are constrained to account A

#### Scenario: Remote bridge owner revalidates forwarded file ownership

- **GIVEN** origin replica A durably resolves an input file to account A
- **AND** the request's HTTP bridge owner runs on replica B
- **WHEN** replica A forwards the request to replica B with authenticated file-owner metadata
- **THEN** replica B MUST freshly resolve the shared durable pin
- **AND** it MUST accept the forwarded owner only when both owner values match
- **AND** a missing, conflicting, tampered, or legacy-unbound proof MUST be rejected before upstream invocation

#### Scenario: Retired WebSocket turn state does not cross accounts

- **GIVEN** a closed upstream WebSocket on account A supplied an account-scoped turn-state token
- **AND** a later movable bare-session frame or marked self-contained goal restart selects account B
- **WHEN** the proxy opens the replacement WebSocket
- **THEN** it removes account A's stale turn-state token before connect
- **AND** account B never receives that token

### Requirement: Bare process-session cap spillover is non-mutating

The system MUST parse process-session and thread headers independently. A bare
process-session mapping and a bounded thread-local mapping MUST use distinct,
header-inaccessible storage identities, so a client-supplied hard turn-state
value cannot alias either derived soft row. A current replica MUST consult a
legacy raw Codex-session key independently even when a namespaced process or
thread row exists. Any raw hit MUST take precedence as hard ownership. If a
resolved file, response, bridge, or other exact owner conflicts with that raw
legacy owner, the request MUST fail closed without creating or rewriting any
of those rows.

A missing thread row MAY use an eligible process-session soft row as its
initial placement preference. If that process row is missing, the first
admitted thread MUST initialize it with insert-if-absent and MUST persist its
own bounded thread row. A concurrent or later thread MUST NOT overwrite that
first-writer process preference. Account-cap spillover or later thread movement
MUST NOT rewrite or delete the process-session mapping or a sibling's thread
mapping. A provisional recovery-probe reservation MUST NOT initialize a
missing process preference, because its thread mapping may still require
rollback and a probing account is not a stable process default. A later normal
admission MAY initialize the missing process preference.

When the mapped account for a bare process-session key is locally capped and
another eligible account is selected, the spillover MUST apply only to that
request. Selection MUST NOT update or delete the stored process-session mapping
because of account-cap spillover. If the mapped account is below cap, normal
sticky selection MUST retain it.

#### Scenario: Capped bare-session owner spills without rebinding

- **GIVEN** a bare process-session mapping points to account A
- **AND** account A is locally capped
- **AND** account B is eligible and below cap
- **WHEN** a self-contained pre-visible request is selected
- **THEN** the request uses account B
- **AND** the stored process-session mapping still points to account A

#### Scenario: Unsaturated bare-session owner retains locality

- **GIVEN** a bare process-session mapping points to eligible account A below its local caps
- **WHEN** a self-contained request is selected
- **THEN** the request uses account A
- **AND** the mapping remains unchanged

#### Scenario: Equal session and turn-state values remain isolated

- **GIVEN** a process-session header and an explicit turn-state header have equal text values
- **WHEN** their affinity mappings are resolved
- **THEN** the process-session mapping uses a source-separated opaque key
- **AND** the explicit turn-state mapping continues to use the legacy raw key as hard ownership

#### Scenario: Derived soft key cannot be reused as raw hard turn state

- **GIVEN** a process-session or thread value has a derived internal storage key
- **WHEN** a client submits the visible representation of that key as a turn-state header
- **THEN** header normalization cannot reproduce the internal storage identity
- **AND** hard turn-state selection cannot read or rewrite the soft row

#### Scenario: Legacy raw mapping remains hard

- **GIVEN** a legacy replica persisted a raw Codex-session mapping
- **WHEN** a current replica receives the matching process or legacy thread header
- **THEN** it does not reinterpret or mutate the legacy raw row as spillable affinity
- **AND** mixed-version operation remains fail-closed for that row

#### Scenario: Coexisting legacy and namespaced rows prefer hard ownership

- **GIVEN** mixed-version replicas created a raw row and a namespaced process or thread row for the same request identity
- **AND** the rows point to different accounts
- **WHEN** a current replica selects the request
- **THEN** the raw row's account is treated as the hard owner
- **AND** neither row is deleted or rewritten by account-cap spillover

#### Scenario: Legacy hard owner conflicts with resolved owner

- **GIVEN** a raw legacy session row points to account A
- **AND** a file, previous response, or bridge resolves to account B
- **WHEN** the request is routed
- **THEN** the service fails with `continuity_owner_conflict`
- **AND** it neither bypasses nor rewrites the raw row

#### Scenario: Process preference seeds only the new thread

- **GIVEN** a process-session soft row points to account A
- **AND** no bounded row exists for thread T
- **WHEN** T is admitted on account A or a safely selected alternate
- **THEN** the admitted account is persisted under T's bounded key
- **AND** the process-session row remains unchanged

#### Scenario: Missing process preference is initialized once

- **GIVEN** no process-session mapping exists
- **WHEN** the first thread is admitted on account A and a concurrent or later thread is admitted on account B
- **THEN** insert-if-absent preserves the first persisted process owner
- **AND** each thread persists only its own bounded locality after that initialization

#### Scenario: Provisional probe placement does not escape rollback

- **GIVEN** neither process nor thread has a bounded mapping
- **WHEN** a probing-account placement persists provisionally and then loses its runtime commit
- **THEN** its thread mutation is restored
- **AND** no immutable process preference is left behind

#### Scenario: Legacy raw owner wins over thread locality

- **GIVEN** a raw legacy Codex row points to account A
- **AND** a bounded thread row points to account B
- **WHEN** the request is routed
- **THEN** the raw row remains hard ownership evidence and account A wins
- **AND** neither mapping is rewritten to reconcile the disagreement

### Requirement: Hard HTTP bridge reconnects remain account-bound after upstream close

When an HTTP responses bridge session uses a hard continuity key such as `turn_state_header` or `session_header`, replay or reconnect handling MUST NOT route the same pending request to a different upstream account solely because the prior upstream WebSocket closed with code `1011`.

Soft-affinity bridge sessions MAY continue to exclude the failed account for transient upstream close recovery when no hard continuity dependency is present.

#### Scenario: session-header bridge replay preserves owner account after 1011

- **GIVEN** an HTTP bridge session is keyed by `session_header`
- **AND** its upstream WebSocket closes with code `1011` before `response.completed`
- **WHEN** the bridge attempts a pre-created replay or reconnect for the pending request
- **THEN** the account selector is called with the current session account as the preferred account
- **AND** the current session account is not excluded solely because of the `1011` close
- **AND** the request is not replayed on another account unless an explicit non-1011 account-failure path requires it

### Requirement: HTTP bridge upstream WebSocket connects use WebSocket-safe headers

When HTTP responses bridge code opens or reconnects an upstream responses WebSocket, it MUST remove HTTP-only and hop-by-hop inbound headers before passing headers to the upstream WebSocket connector.

The upstream responses WebSocket header builder MUST NOT forward HTTP Responses API beta tokens such as `responses=experimental`; it MUST send the responses WebSocket beta token required by the upstream WebSocket protocol.

The sanitized header set MUST preserve Codex continuity headers such as `session_id`, `x-codex-session-id`, and `x-codex-turn-state` when those headers are required for affinity.

#### Scenario: HTTP bridge create filters HTTP request headers

- **GIVEN** an HTTP responses bridge request contains HTTP request headers such as `accept`, `accept-encoding`, `content-type`, `connection`, `authorization`, `cookie`, or `host`
- **WHEN** the bridge opens a new upstream responses WebSocket
- **THEN** those HTTP-only or hop-by-hop headers are not forwarded to the upstream WebSocket connector
- **AND** the continuity `session_id` header remains available for upstream affinity

#### Scenario: HTTP bridge reconnect filters HTTP request headers

- **GIVEN** an HTTP responses bridge session is reconnecting an upstream responses WebSocket
- **AND** the session stores HTTP request headers from the original downstream request
- **WHEN** reconnect prepares the upstream WebSocket headers
- **THEN** HTTP-only and hop-by-hop headers are filtered before the upstream WebSocket connector is called
- **AND** the selected `x-codex-turn-state` remains available for upstream continuity

#### Scenario: upstream WebSocket beta header excludes HTTP Responses token

- **GIVEN** a responses WebSocket connect request receives `OpenAI-Beta: responses=experimental`
- **WHEN** upstream WebSocket headers are built
- **THEN** `responses=experimental` is not forwarded
- **AND** `responses_websockets=2026-02-06` is present

### Requirement: Unanchored process-session concurrency uses independent bridge lanes

When multiple Responses requests share a process-level session header but
carry neither `previous_response_id` nor nonblank turn-state continuity, the
service MUST NOT queue an independent request behind an active response-create
gate. If the canonical bridge is still being created, reserved by another
request before submit, already has a visible request, or belongs to a different
model class, the service MUST create a server request-scoped bridge lane. The
lane identity MUST NOT depend on a client-controlled request ID. The fork MUST
leave the canonical bridge and its model metadata unchanged.

When such requests carry nonblank `thread-id`, each thread MUST have a stable
canonical bridge identity derived from process and thread identity regardless
of `prompt_cache_key`; distinct threads MUST remain isolated even when they
execute sequentially, and repeated requests from one thread MUST retain one
identity. Requests without `thread-id` MUST retain the legacy session-header
identity, including the established explicit-prompt-cache composition.

A pre-submit handoff reservation MUST protect its bridge from idle pruning and
capacity eviction, and any cancellation or error between lookup and visible
submission MUST release it. Owner forwarding MUST preserve whether a
session-header, thread-header, or internal-fork request was unanchored instead
of treating a proxy-generated downstream turn-state as an explicit client
anchor, but MUST NOT attach that v2-only state to prompt-cache or unrelated
affinity families. It MUST fail closed when a mixed-version hop cannot
authenticate required unanchored state. The v2 primary signature MUST bind
whether client-IP metadata was present, while the companion signature MUST bind
its value. When the canonical owner itself creates a fork for a forwarded
request, it MUST own that fork locally instead of re-hashing it into another
forwarding hop. Explicitly anchored owner forwards MUST retain the
legacy-compatible primary signature during rolling upgrades, and a receiving
instance MUST reject ambiguous delimiter-bearing legacy fields. Durable aliases
derived from the forked lane MUST retain hard owner and account continuity. If
durable ownership fencing rejects a stale owner's new alias, the stale owner
MUST remove the matching local alias without removing a newer local
generation's mapping.

#### Scenario: sequential child agent does not reuse parent bridge history

- **GIVEN** a parent and child Codex agent share one process session and `prompt_cache_key`
- **AND** each agent supplies its own stable `thread-id`
- **WHEN** the child starts after the parent's visible request has completed
- **THEN** the child uses a different bridge identity from the parent
- **AND** another request from that same child keeps the child's bridge identity

#### Scenario: Background requests do not block behind a foreground turn

- **GIVEN** a foreground request is active on a session-header or thread-header bridge
- **WHEN** two unanchored background requests arrive with the same canonical identity
- **THEN** each background request uses an independent response-create gate
- **AND** neither request waits for the foreground response to complete
- **AND** the foreground bridge's model metadata remains unchanged

#### Scenario: Lookup-to-submit requests remain isolated

- **GIVEN** an unanchored request has reserved an idle canonical bridge but has not yet made queued activity visible
- **WHEN** another unanchored request arrives with the same canonical identity and client request ID
- **THEN** the second request uses a distinct server-scoped bridge lane
- **AND** it does not reuse the reserved canonical bridge

#### Scenario: Durable refresh publishes the handoff reservation

- **GIVEN** an unanchored request reuses an idle durable canonical bridge
- **WHEN** refreshing the durable lease yields before lookup returns
- **THEN** the canonical bridge is already reserved for that request
- **AND** a concurrent unanchored request uses a distinct server-scoped lane

#### Scenario: Cancelled pre-submit handoff does not strand a reservation

- **GIVEN** an unanchored request is reusing an idle canonical bridge
- **WHEN** the request is cancelled after claiming the bridge but before queued activity becomes visible
- **THEN** the canonical bridge remains unreserved
- **AND** later requests are not forced onto fork lanes by the cancelled lookup

#### Scenario: Payload preparation failure does not strand a reservation

- **GIVEN** an unanchored request has reserved an idle canonical bridge
- **WHEN** anchor injection, trimming, or payload validation fails before submission
- **THEN** request-scope cleanup releases the reservation
- **AND** later requests may reuse the canonical bridge

#### Scenario: Remote owner preserves unanchored concurrency

- **GIVEN** an unanchored request is forwarded to the canonical bridge owner
- **AND** the proxy generated a downstream turn-state for response aliasing
- **WHEN** the owner receives the forwarded request while the canonical lane is active
- **THEN** the owner still treats the request as unanchored
- **AND** the request uses an independent bridge lane
- **AND** the pre-submit handoff remains reserved until submission becomes visible

#### Scenario: Owner-side fork does not start a second forwarding hop

- **GIVEN** an unanchored request has reached its canonical owner
- **AND** that owner creates an independent fork because the canonical lane is active
- **WHEN** rendezvous hashing the generated fork key would select another instance
- **THEN** the canonical owner creates and durably claims the fork locally
- **AND** the request is not rejected as a forwarding loop

#### Scenario: Blank turn-state is not an anchor

- **GIVEN** a request has process/thread identity and an empty or whitespace-only turn-state header
- **WHEN** the request is forwarded to its owner
- **THEN** the signed forwarding context marks the original request as unanchored
- **AND** the generated downstream turn-state does not collapse it onto the canonical gate

#### Scenario: Forwarding downgrade fails closed

- **GIVEN** an owner-forward request requires unanchored concurrency semantics
- **WHEN** the signed unanchored boolean is changed, removed, or repacked into affinity fields, or either instance only supports the legacy signature
- **THEN** the owner-forward hop fails closed
- **AND** the request is not attached to the shared canonical response-create gate

#### Scenario: Anchored forwarding remains rolling-upgrade compatible

- **GIVEN** an owner-forward request carries explicit previous-response or turn-state continuity
- **WHEN** the origin and owner run different bridge protocol versions
- **THEN** the primary signature remains valid under the legacy contract
- **AND** the anchored request can continue without weakening unanchored fail-closed behavior

#### Scenario: Prompt-cache forwarding remains rolling-upgrade compatible

- **GIVEN** an unanchored first-turn request uses a prompt-cache affinity lane
- **WHEN** that request is forwarded to its canonical owner
- **THEN** the origin does not attach session/thread-header unanchored v2 state
- **AND** an older owner may accept the legacy-compatible forwarding contract

#### Scenario: Legacy session-header canonical lane proves its turn-state anchor

- **GIVEN** a legacy-signed owner forward has no previous-response ID and its durable canonical key is still `session_header`
- **WHEN** its forwarded turn state is a registered durable alias for that exact canonical lane
- **THEN** the current owner accepts it as anchored continuity
- **AND** an unknown turn state or an alias for another canonical lane fails closed with `bridge_forward_upgrade_required`

#### Scenario: Legacy proof precedes compact and bridge fallback branches

- **GIVEN** a legacy-signed owner forward requires turn-state anchor proof
- **WHEN** the request contains a terminal compaction trigger or bypasses the websocket bridge
- **THEN** exact alias proof runs before compact, HTTP fallback, admission, or upstream work

#### Scenario: Current origin proves a turn-state alias before legacy owner forwarding

- **GIVEN** a current origin resolves a nonblank turn state only through a shared `session_header` durable lane
- **WHEN** that request would be forwarded to another owner with the legacy signature contract
- **THEN** the origin proves an exact turn-state alias row for that canonical lane before sending the owner request
- **AND** an unknown alias fails closed with `bridge_forward_upgrade_required`

#### Scenario: Latest-state metadata is not proof of alias registration

- **GIVEN** a durable session records a latest turn state but has no matching turn-state alias row
- **WHEN** that value is presented by a legacy-signed owner forward
- **THEN** the owner rejects it with `bridge_forward_upgrade_required`

#### Scenario: Stale owners cannot register continuity aliases after takeover

- **GIVEN** durable ownership advanced to a new owner epoch
- **WHEN** the stale owner attempts to register a turn-state or previous-response alias with its old epoch
- **THEN** alias registration writes nothing
- **AND** the stale owner removes the rejected value from its local alias index
- **AND** a newer local generation's mapping for the same value remains intact
- **AND** the stale value cannot satisfy legacy anchor proof

#### Scenario: Ambiguous legacy signature fields fail closed

- **GIVEN** a legacy owner-forward signature contains a delimiter in any signed header field
- **WHEN** field boundaries are repacked without changing the legacy joined byte string
- **THEN** a current owner rejects the forwarding context as invalid
- **AND** the repacked affinity kind cannot weaken hard continuity

#### Scenario: V2 client-IP metadata cannot be removed or blanked

- **GIVEN** an unanchored v2 owner-forward request carries signed client-IP metadata
- **WHEN** both client-IP headers are removed, the value is blanked, or the value is changed
- **THEN** the owner rejects the forwarding context as invalid
- **AND** a genuinely no-IP v2 request remains valid

#### Scenario: Durable fork continuation remains owner-bound

- **GIVEN** a forked lane has produced a durable turn-state or previous-response alias
- **WHEN** a later request resolves that alias on another instance
- **THEN** the request follows the hard owner-bound continuity path
- **AND** the original account binding is preserved

#### Scenario: Explicit continuation is not split

- **WHEN** a request carries `previous_response_id` or a turn-state header
- **THEN** the service keeps the request on the hard owner-bound continuity path
- **AND** it does not apply unanchored parallel-session isolation

### Requirement: Unusable account transitions remove persistent affinity bindings

The system SHALL remove persistent affinity bindings when an account becomes
permanently unusable because it requires reauthentication or is deactivated.
This includes durable sticky-session mappings and durable HTTP bridge aliases.
Any durable HTTP bridge rows closed by this transition MUST clear account
ownership, owner leases, and stored continuity anchors so follow-up requests
cannot resolve stale turn-state or previous response aliases through the closed
row.

#### Scenario: Reauthentication requirement clears bridge continuity

- **GIVEN** an account has sticky-session mappings and durable HTTP bridge aliases
- **AND** a bridge row stores the latest turn state and previous response
- **WHEN** the account is marked `reauth_required`
- **THEN** sticky-session mappings for the account are deleted
- **AND** durable HTTP bridge aliases for the account's bridge rows are deleted
- **AND** the bridge rows are closed without account ownership, live owner lease, or stored continuity anchors

#### Scenario: Failed compare-and-swap status transition keeps affinity bindings

- **GIVEN** an account has sticky-session mappings and durable HTTP bridge aliases
- **WHEN** a conditional account status update does not match the expected current row state
- **THEN** the account's sticky-session mappings and durable HTTP bridge aliases remain unchanged

### Requirement: Durable bridge lease writes are fenced
All durable HTTP bridge session lease writes — renewal, release, and continuity-alias registration — MUST be executed as single fenced statements conditioned on the caller's `(owner_instance_id, owner_epoch)` so a fenced-out caller mutates nothing. A fenced-out renewal or release MUST leave the row (owner, lease, state, and `latest_turn_state` / `latest_response_id` continuity anchors) unchanged and MUST report the current owner snapshot to the caller.

#### Scenario: Stale-epoch renewal does not overwrite the new owner
- **GIVEN** replica B took over a durable bridge session, advancing its owner epoch
- **WHEN** replica A renews the session with its stale epoch
- **THEN** the row still shows replica B's ownership, lease, and continuity anchors
- **AND** replica A receives a snapshot identifying replica B as the current owner

#### Scenario: Stale-epoch release does not clear the new owner's lease
- **GIVEN** replica B took over a durable bridge session, advancing its owner epoch
- **WHEN** replica A releases the session with its stale epoch
- **THEN** the row keeps replica B's ownership and ACTIVE state
- **AND** replica A receives a snapshot identifying replica B as the current owner

### Requirement: Fenced-out replicas evict their local bridge session
When a replica discovers through a fenced renewal or fenced alias write that another instance or epoch owns the durable session, it MUST close its local in-memory bridge session — closing the upstream websocket and releasing the account lease — instead of adopting the new epoch and continuing to serve. A replica MUST also reconcile durable ownership for local sessions whose lease is past its TTL on the ring-heartbeat cadence and close any session that has been fenced out, so orphaned upstream connections and account leases are bounded by the lease TTL rather than the idle TTL.

#### Scenario: Fenced-out renewal closes the local session
- **GIVEN** replica A holds a local bridge session and replica B took over the durable row
- **WHEN** replica A's lease renewal is fenced out
- **THEN** replica A detaches and closes the local session, releasing its account lease and upstream websocket
- **AND** the request fails with the retryable bridge-instance-mismatch error instead of riding the fenced-out session

#### Scenario: Heartbeat reconciliation closes fenced-out idle sessions
- **GIVEN** replica A holds an idle local bridge session whose durable lease expired
- **AND** replica B has since claimed the durable row
- **WHEN** replica A's heartbeat reconciliation sweep runs
- **THEN** replica A closes the fenced-out local session
- **AND** local sessions still owned by replica A are left untouched

#### Scenario: Reconciliation lookups survive large candidate sets
- **GIVEN** more local sessions are past the lease TTL than fit in one database `IN (...)` parameter list
- **WHEN** the reconciliation sweep batch-loads the durable rows
- **THEN** the lookup is chunked so every candidate resolves and fenced-out sessions are still evicted

### Requirement: Abandoned durable bridge rows are purged
The background cleanup loop MUST delete ACTIVE and DRAINING `http_bridge_sessions` rows whose lease is expired and whose `last_seen_at` predates the retention cutoff, deleting their aliases in the same pass, so crashed-owner and abandoned-drain rows do not accumulate. Rows with an unexpired lease or recent activity MUST NOT be deleted so crash takeover and drain recovery keep their continuity anchors. The retention cutoff MUST be at least the longest effective bridge session reuse window — the maximum of the prompt-cache affinity max age, the prompt-cache bridge idle TTL, the codex bridge idle TTL, and the base bridge idle TTL — so an idle-but-still-reusable local session never loses its ACTIVE durable row and aliases while it can still be reused.

#### Scenario: Expired abandoned rows are purged with their aliases
- **WHEN** the cleanup loop runs
- **AND** an ACTIVE row's lease expired and its `last_seen_at` is older than the retention cutoff
- **THEN** the row and its aliases are deleted

#### Scenario: Recent or live-lease rows survive the purge
- **WHEN** the cleanup loop runs
- **AND** a row holds an unexpired lease or has `last_seen_at` within the retention cutoff
- **THEN** the row and its aliases are preserved

#### Scenario: In-reuse-window prompt-cache sessions keep their durable row
- **GIVEN** the prompt-cache bridge idle TTL exceeds the prompt-cache affinity max age
- **WHEN** the cleanup loop runs against an ACTIVE row whose lease expired but whose `last_seen_at` is within the prompt-cache bridge idle TTL
- **THEN** the row and its aliases are preserved so a local reuse keeps its durable ownership and continuity anchors

### Requirement: Restart removes stale owned bridge state

On startup, the system MUST remove ordinary persisted HTTP bridge session rows owned by the configured bridge instance from the previous process. A recent server-namespaced account-neutral recovery row MUST instead be changed to ownerless DRAINING with an expired lease while preserving its aliases and original activity timestamp. The cleanup MUST remove ownerless ACTIVE/DRAINING rows with expired leases once their activity predates the abandoned-row retention cutoff. Deleted rows MUST lose their associated durable bridge aliases. The cleanup MUST NOT remove sticky-session mappings or rows owned by other bridge instances.

#### Scenario: First request after restart starts without stale bridge state

- **GIVEN** the previous process left ordinary durable HTTP bridge rows owned by the configured instance
- **WHEN** the next process completes startup
- **THEN** those durable bridge rows and their aliases MUST be removed before accepting requests
- **AND** the first request MUST create fresh bridge state instead of reusing the previous process's bridge row
- **AND** sticky-session mappings MUST remain available

#### Scenario: Recent verified recovery proof survives restart only until retention

- **GIVEN** the previous process left a recent server-namespaced account-neutral recovery row with task-specific aliases
- **WHEN** the next process completes startup
- **THEN** the row MUST become ownerless DRAINING with an expired lease
- **AND** its task-specific aliases and original activity timestamp MUST remain unchanged
- **AND** a later startup or abandoned-row cleanup MUST remove the row and aliases after the activity timestamp passes the retention cutoff

#### Scenario: Ownerless stale rows with expired leases are removed

- **GIVEN** durable HTTP bridge rows exist with no owner instance, expired leases, and activity older than the abandoned-row retention cutoff
- **WHEN** the process completes startup
- **THEN** those rows and their aliases MUST be removed
- **AND** rows owned by other instances MUST NOT be removed

#### Scenario: Sticky-session mappings are preserved

- **GIVEN** sticky-session mappings exist for the account
- **WHEN** the process completes startup and purges stale bridge rows
- **THEN** sticky-session mappings MUST remain available for account affinity

### Requirement: Trusted capability requirements are monotonic across lineage

Before dispatch, the proxy MUST persist an authenticated `trusted_cyber`
requirement as API-key-scoped, domain-separated opaque hashes for every known
session, accepted or synthesized turn-state, previous-response, and Codex task
lineage alias. Marker writes MUST be monotonic and MUST NOT store raw lineage
or account identifiers.

A later authenticated request MUST restore REQUIRED before account selection
when any presented alias matches under the same API-key scope. It MUST persist
that requirement onto newly generated aliases. A marker under one API key MUST
NOT establish REQUIRED under another key. Read or write uncertainty MUST fail
before ordinary dispatch.

When `response.created` first reveals a response ID for a durably REQUIRED
request, the proxy MUST persist the upstream and downstream-visible response
aliases before forwarding that created event. If this propagation fails, the
proxy MUST NOT expose the unpersisted response ID, replay the accepted request,
or penalize the upstream account.

#### Scenario: No-echo reconnect remains required
- **WHEN** a capability-bearing direct WebSocket turn persists an accepted
  session identity and a proxy-synthesized turn state
- **AND** a new connection presents the same session identity without the
  capability marker or generated turn state
- **THEN** REQUIRED is restored before its first account selection

#### Scenario: Echoed synthesized turn state remains required
- **WHEN** the reconnect instead echoes the accepted synthesized turn state
- **THEN** REQUIRED is restored before its first account selection

#### Scenario: Response-only reconnect remains required
- **WHEN** a capability-bearing turn exposes a response ID only after upstream
  acceptance
- **AND** a new connection presents only that `previous_response_id` under the
  same API key, without a matching session or turn state
- **THEN** REQUIRED is restored before its first account selection

#### Scenario: Requirement survives a fresh service instance
- **WHEN** a new repository and proxy service instance reads an alias marked by
  an earlier instance
- **THEN** the alias still restores REQUIRED

#### Scenario: API-key scope is isolated
- **WHEN** API key B presents the same visible lineage identifier previously
  marked under API key A
- **THEN** key A's marker does not establish REQUIRED for key B

#### Scenario: Persistence uncertainty cannot downgrade
- **WHEN** required lineage cannot be read or established durably
- **THEN** the request fails before ordinary account selection or dispatch

### Requirement: Compact previous_response_id anchors are account-scoped

codex-lb MUST NOT inject a compact `previous_response_id` anchor whose owning account differs from the account that will serve the request.

The HTTP-bridge compact-anchor injection reduces payload size by replacing
already-stored history with a proxy-supplied `previous_response_id`, and a
`previous_response_id` can only be resumed by the account that created it. The
rule applies to every injection site that runs after the serving account is
bound: the session-level anchor (`session.last_completed_response_id`) and the
owner-forward recovery anchor (`durable_lookup.latest_response_id`, injected
after a rebind that is allowed to land on a different account).

codex-lb MUST record the account that owns `last_completed_response_id` whenever
that value is set — from a real upstream `response.completed` (the session's
current account) or from a durable-session restore (the durable owner account) —
and keep the two in sync.

Injection sites that run before the serving account is bound stay covered by the
existing required-continuity-owner pin, which fails the request rather than
serving a proxy-injected anchor on a different account.

#### Scenario: Anchor injected when the serving account owns it

- **WHEN** a Codex session follow-up turn is eligible for compact-anchor injection
- **AND** the account that owns `last_completed_response_id` equals the session's
  serving account
- **THEN** codex-lb injects `previous_response_id = last_completed_response_id`
  and trims the already-stored history prefix

#### Scenario: Anchor skipped after cross-account failover

- **WHEN** a Codex session follow-up turn is eligible for compact-anchor injection
- **AND** the account that owns `last_completed_response_id` differs from the
  session's serving account (for example the session failed over after the durable
  owner account became unavailable)
- **THEN** codex-lb MUST NOT inject the anchor
- **AND** codex-lb resends the full history to the serving account so continuity
  is preserved without an unresolvable `previous_response_id`
- **AND** the request MUST NOT stall waiting for a `response.created` that upstream
  will never send for an anchor the serving account does not own

#### Scenario: Owner-forward recovery anchor skipped after a cross-account rebind

- **WHEN** an owner forward fails and the local recovery rebind binds the session
  to an account other than the durable record's owner
- **AND** the durable record still carries a `latest_response_id` the recovery
  request would otherwise anchor on
- **THEN** codex-lb MUST NOT inject that anchor
- **AND** the recovery request keeps its full input instead of a trimmed suffix

#### Scenario: Declined anchors are observable

- **WHEN** codex-lb declines a compact anchor because the serving account does not
  own it
- **THEN** codex-lb logs a `cross_account_anchor_declined` bridge event naming the
  injection site, the anchor's owning account, and the full-history-resend outcome

### Requirement: Idle bridge sessions are swept without request traffic

The system MUST evict idle HTTP-bridge sessions on every replica independently of whether that replica is receiving bridge requests. The sweep MUST reuse the same eligibility the request path applies — a session with pending or queued work, an admission waiter, a handoff in progress, or an unanchored reservation, and a session still inside its idle TTL, MUST NOT be evicted — and MUST close evicted sessions through the existing bounded close path so a slow upstream-reader cancellation cannot block the caller. A sweep failure MUST NOT interrupt the loop that drives it, and MUST NOT prevent the other per-replica bridge upkeep that shares that loop from running.

#### Scenario: A replica with no bridge traffic still evicts idle sessions

- **GIVEN** a replica holds an idle bridge session past its idle TTL and receives no further bridge requests
- **WHEN** the sweep runs
- **THEN** the session is detached from the registry and closed, releasing its upstream WebSocket

#### Scenario: Sweep eligibility matches the request path

- **GIVEN** a session with pending work whose idle TTL has elapsed, and a session used moments ago
- **WHEN** the sweep runs
- **THEN** neither session is evicted

#### Scenario: One failing upkeep pass does not skip the other

- **GIVEN** the durable-ownership reconcile raises on a heartbeat tick
- **WHEN** that tick runs
- **THEN** the idle sweep still runs and the heartbeat loop continues

#### Scenario: Sweeping an empty registry does nothing

- **WHEN** the sweep runs with no registered bridge sessions
- **THEN** no session is closed and no cleanup work is scheduled

### Requirement: File-pin required owner does not rewrite thread locality

A resolved live `input_file.file_id` pin MUST be selected as the required owner without consulting or rewriting the current-Codex thread-scoped soft mapping. The process-session compatibility row MAY still be consulted as independent hard ownership. If that raw row conflicts with the pin account, the request MUST fail closed. A missing process-session preference MAY still initialize insert-if-absent.

#### Scenario: File-pinned request owner overrides thread locality

- **GIVEN** a request carries a `thread-id` whose bounded mapping points to account A
- **AND** its `input_file.file_id` is durably pinned to account B
- **WHEN** the request is routed
- **THEN** account B is treated as the required owner
- **AND** the thread mapping is neither consulted as an owner nor rewritten

#### Scenario: File pin still conflicts with a raw process-session owner

- **GIVEN** a raw process-session `codex_session` row points to account A
- **AND** a live file pin points to account B
- **WHEN** the request is routed
- **THEN** the service fails with `continuity_owner_conflict` before upstream dispatch
- **AND** neither the raw row nor the thread row is rewritten

### Requirement: Thread-scoped current Codex restarts still abandon a raw process-session owner

A self-contained Codex goal-continuation restart that also carries a distinct `thread-id` MUST still be eligible for the existing process-session abandonment exception. The request's thread-scoped locality source MUST NOT prevent the one-shot abandonment capability or the compare-and-set retirement of the raw process-session row.

The retirement write MUST remain scoped to `session_header`
interpretation of that raw key. An explicit `turn_state` lookup of the
same text MUST stay hard-bound to the stored account. After a
successful retirement, later same-thread turns that have no new hard
owner MUST keep continuity on the replacement account and MUST NOT
treat the `session_header`-abandoned raw row as live hard ownership.

Ordinary incremental, file-pinned, conversation-bound, and unresolved
tool-state requests MUST remain fail-closed on their required owner.

#### Scenario: Goal restart with process session and thread-id abandons the unavailable raw owner

- **GIVEN** a process-session identifier has a raw legacy `codex_session` mapping to account A
- **AND** account A is paused, rate-limited, or quota-exceeded
- **AND** account B is eligible
- **AND** the request also carries a distinct `thread-id`
- **WHEN** Codex sends the recognized goal-continuation marker with an account-neutral self-contained full resend and no other continuity dependency
- **THEN** the proxy marks the still-current raw mapping to account A abandoned only for process-session interpretation
- **AND** it routes the restarted turn to account B
- **AND** subsequent same-thread continuity remains on account B

#### Scenario: Thread-id on a goal restart cannot erase colliding explicit turn-state ownership

- **GIVEN** a raw legacy `codex_session` row was written as explicit turn-state ownership for account A
- **AND** a later request carries the same text as a process-session header plus a distinct `thread-id`
- **WHEN** a marked self-contained goal restart abandons that text for process-session interpretation
- **THEN** the restart may select account B
- **AND** an explicit turn-state lookup of the same text remains hard-bound to account A

#### Scenario: Account-dependent thread-scoped restart stays fail-closed

- **GIVEN** a process-session identifier has a raw legacy mapping to unavailable account A
- **AND** the request carries a distinct `thread-id`
- **AND** the body has a previous response, conversation, file pin, or unresolved tool state
- **WHEN** the request is selected
- **THEN** the request fails closed on account A
- **AND** the raw mapping is neither deleted nor rebound

### Requirement: Same-owner sticky refresh writes are coalesced

When selection retains the existing pinned owner of a TTL-based sticky mapping, the
mapping write exists only to advance the mapping's freshness timestamp. The system
MUST skip that write when the same request's owner lookup already observed the row
with a freshness timestamp younger than a bounded skip window, so concurrent requests
of one hot session do not serialize on the same row's lock.

The skip window MUST NOT exceed 1% of the mapping's configured TTL and MUST NOT
exceed 15 seconds, so a mapping's effective expiry — on both the read-path TTL check
and the background cleanup loop — moves at most that window earlier than today's
write-per-request behavior.

The skip decision MUST be derived from row state observed in the current request's
database lookup, not from cross-request in-process state, so any number of workers or
replicas remain correct. The lookup MUST report the skip as a deadline (the observed
freshness timestamp plus the skip window), and the write path MUST revalidate that
deadline against the clock at the moment the write would otherwise be issued — a
deadline that lapsed while the request was being admitted no longer authorizes a
skip. A row whose observed freshness timestamp lies in the future (clock skew or a
restored row) MUST NOT be skippable at all.

A skip MUST apply only to a pure freshness rewrite. The following writes MUST remain
immediate and unconditional: rebinding the mapping to a different account, deleting
the mapping, restoring a provisional owner after failed admission, initializing a
seed mapping, and any upsert against a row carrying an abandonment marker (whose
write also clears the marker columns). In particular, a retention write that would
initialize a missing seed mapping MUST NOT be skipped even when the retained row
itself was observed fresh, because the seed initialization piggybacks on that write.
A raw legacy owner that shadows the namespaced row MUST NOT inherit the namespaced
row's freshness observation.

#### Scenario: Hot same-owner retention skips the redundant refresh write

- **GIVEN** a `prompt_cache` mapping pinned to an eligible account
- **AND** the request's owner lookup observed the row fresher than the skip window
  with no abandonment marker
- **WHEN** selection retains the pinned account
- **THEN** the request routes to the pinned account
- **AND** no sticky-session write is issued for the retention

#### Scenario: Retention outside the skip window refreshes write-through

- **GIVEN** a `prompt_cache` mapping pinned to an eligible account
- **AND** the row's freshness timestamp is older than the skip window but inside the TTL
- **WHEN** selection retains the pinned account
- **THEN** the mapping's freshness timestamp is advanced by a write

#### Scenario: Rebind is never coalesced

- **GIVEN** a soft mapping whose row was observed fresher than the skip window
- **WHEN** selection rebinds the mapping to a different account
- **THEN** the rebind is persisted immediately

#### Scenario: A skipped refresh does not clobber a concurrent rebind

- **GIVEN** a request that observed a fresh same-owner row and skipped its refresh write
- **AND** a concurrent request rebinds the same mapping to another account
- **WHEN** both requests complete
- **THEN** the mapping's owner is the rebind target

#### Scenario: A retention that must initialize a missing seed is never skipped

- **GIVEN** a thread mapping observed fresher than the skip window
- **AND** the corresponding process seed mapping does not exist
- **WHEN** selection retains the thread mapping's pinned account
- **THEN** the retention write is issued and the seed mapping is initialized

#### Scenario: A deadline that lapsed during admission writes through

- **GIVEN** a request whose lookup observed the row inside the skip window
- **AND** admission latency carried the request past the observed skip deadline
- **WHEN** the retention write would be issued
- **THEN** the deadline is revalidated and the freshness write is performed

#### Scenario: A future freshness timestamp is never skippable

- **GIVEN** a mapping whose freshness timestamp lies ahead of the current clock
- **WHEN** the owner lookup evaluates the skip window
- **THEN** no skip deadline is reported and retention writes through

### Requirement: File-pin reconnect provenance preserves existing routing eligibility

HTTP-bridge reconnect MUST mark a live required file-pin owner as a continuity
owner. Existing account-neutral replay provenance MUST remain unchanged. A
non-file previous-response or other require-preferred owner MUST retain its
ordinary required-preferred provenance and its existing single-account and
API-key assignment-scope eligibility semantics.

#### Scenario: File-pin reconnect carries continuity provenance

- **GIVEN** a live file pin requires `account_a` during HTTP-bridge reconnect
- **WHEN** reconnect selects an account
- **THEN** it MUST pass `account_a` as a required preferred account
- **AND** it MUST mark `account_a` as a continuity owner
- **AND** it MUST disable fallback to another account

#### Scenario: Previous-response owner retains required-preferred semantics

- **GIVEN** a non-file previous-response reconnect requires `account_b`
- **WHEN** reconnect selects an account
- **THEN** it MUST pass `account_b` as a required preferred account
- **AND** it MUST NOT newly mark `account_b` as a continuity owner
- **AND** dashboard single-account routing MUST NOT narrow that required owner
- **AND** API-key assignment scope MUST still determine its eligibility

### Requirement: File-pin provenance preserves single-account behavior without weakening scope

A required file-pin continuity owner MUST bypass dashboard single-account
narrowing, matching the existing required-preferred behavior. It MUST NOT
become eligible outside API-key assignment scope, and security authorization
scope MUST remain unchanged.

#### Scenario: Dashboard account differs from in-scope file owner

- **GIVEN** dashboard single-account routing selects `account_x`
- **AND** an in-scope live file pin requires `account_a`
- **WHEN** reconnect selection runs
- **THEN** it MUST select only `account_a`
- **AND** it MUST NOT narrow the lookup to `account_x`

#### Scenario: File owner is outside API-key assignment scope

- **GIVEN** a live file pin requires `account_a`
- **AND** the API key assignment scope excludes `account_a`
- **WHEN** reconnect selection runs
- **THEN** `account_a` MUST remain ineligible
- **AND** selection MUST NOT serve the reconnect from an out-of-scope account

### Requirement: Canonical prompt-cache bridges preserve hard replica continuity

When durable lookup resolves an incoming turn-state or previous-response
reference to a live bridge whose canonical key is `prompt_cache`, the origin
replica MUST treat that request as hard bridge continuity for replica-owner
routing. If the live owner is another reachable replica, the origin MUST use
the authenticated internal owner-forward transport and MUST NOT attempt a soft
local prompt-cache rebind. Preserving the canonical prompt-cache key MUST NOT
weaken the hard continuation evidence or expose `bridge_instance_mismatch` for
an ordinary cross-replica continuation. A request carrying only prompt-cache
locality and no hard continuation evidence MUST retain the existing soft local
rebind behavior. Explicit recovery paths that have already established that
owner forwarding is unavailable MAY retain their bounded local-rebind
behavior.

#### Scenario: Turn-state continuation forwards to the canonical prompt-cache owner

- **GIVEN** a turn-state alias resolves to a live bridge canonically keyed by prompt cache on replica A
- **WHEN** the continuation arrives on replica B
- **THEN** replica B forwards the request internally to replica A
- **AND** it does not attempt to claim the canonical bridge locally
- **AND** replica B leaves no local inflight creation reservation for the forwarded bridge key

#### Scenario: Previous-response continuation forwards to the canonical prompt-cache owner

- **GIVEN** a previous-response reference resolves to a live bridge canonically keyed by prompt cache on replica A
- **WHEN** the continuation arrives on replica B
- **THEN** replica B forwards the request internally to replica A
- **AND** the client does not receive `bridge_instance_mismatch`

#### Scenario: Prompt-cache-only locality remains soft

- **GIVEN** a request has prompt-cache locality but no turn-state, previous-response, or other hard continuity evidence
- **WHEN** its locality owner is another replica
- **THEN** the receiving replica may use the existing soft local-rebind path

### Requirement: Detached durable bridge rows are not continuity owner evidence

When account invalidation (deactivation, re-authentication demand, proxy-binding change, or deletion) detaches a durable HTTP-bridge row, leaving it `CLOSED` with no owner account, no owner instance, and no turn-state or previous-response anchor, durable request-target lookup MUST NOT report that row as a lookup hit, whether resolved by canonical key or by alias. A request whose only durable evidence would have been such a row MUST proceed as a request without durable bridge state, and its claim MUST re-own the same canonical row. A `CLOSED` row that still names its owner account MUST remain durable owner evidence.

#### Scenario: Hard thread continuation survives owner account invalidation

- **GIVEN** a Codex `thread_header` bridge row was detached because its owner account was deactivated
- **AND** the account was later reactivated
- **WHEN** the client continues that thread without `previous_response_id`
- **THEN** the durable lookup reports no durable row for the thread
- **AND** the request is served by ordinary account selection instead of failing closed with `previous_response_owner_unavailable`
- **AND** the selected account's claim reuses the detached canonical row

#### Scenario: Ordinarily released closed row keeps its owner

- **GIVEN** a bridge row was released normally and is `CLOSED` while still naming its owner account and latest response anchor
- **WHEN** a request resolves that canonical key
- **THEN** the durable lookup still returns the row with its owner account

### Requirement: Owner forwarding rejects illegal reconstructed header metadata

Owner-forwarded HTTP bridge requests MUST validate reconstructed bridge
metadata before building signatures or posting headers to another owner.
Metadata values that become signed bridge headers MUST NOT contain illegal HTTP
header control characters. If original affinity, downstream turn-state,
file-owner, client-IP, origin/target instance, or reservation metadata contains
such a character, the proxy MUST fail closed with the structured
`bridge_forward_invalid` error instead of sending the owner request. Ordinary
client headers with illegal HTTP control characters MUST be omitted from the
forwarded header map.

#### Scenario: Unsafe reservation metadata fails closed

- **GIVEN** an owner-forward request carries API-key reservation metadata
- **AND** one reservation field contains an illegal HTTP header control
  character
- **WHEN** the origin builds the owner-forward request
- **THEN** it returns `bridge_forward_invalid`
- **AND** it does not omit only the reservation headers while keeping the owner
  as reservation-settlement authority

#### Scenario: Unsafe client header is omitted

- **GIVEN** an owner-forward request includes an ordinary client header with an
  illegal HTTP header control character
- **WHEN** the origin builds the owner-forward request
- **THEN** that client header is not forwarded
- **AND** the signed bridge-forward metadata remains valid

### Requirement: Isolated accounts release their soft sticky owners

While an account is in the overload **isolation** stage (see `account-routing`), a `prompt_cache`, `sticky_thread` or `codex_session` mapping pinned to it MUST be treated as a fresh admission: selection MUST evaluate the overload-free candidates with the configured strategy and, when one is selectable, MUST route the request there and rebind the mapping to the selected account so later turns do not return to the isolated owner. When no overload-free candidate is selectable (lone account, every sibling backed off, or the strategy rejects the overload-free pool) the pinned owner MUST be kept. A soft backoff below the isolation stage MUST NOT release an established owner. When the released owner is also above the sticky reallocation budget threshold, the replacement MUST be chosen with the secondary-budget filter applied, as for a budget reallocation. A replacement chosen for a released owner MUST satisfy the per-account concurrency caps even where the owner itself is cap-exempt (bare `codex_session` mapping without cap spillover). A bare `codex_session` owner that is both at its account cap with spillover enabled and isolated MUST be rebound to the spillover target rather than preserving the mapping request-locally. Required owners resolved from hard continuity sources (`previous_response_id`, live or durable bridge ownership, file pins, turn-state rows) MUST NOT be released by this rule. A process-session preference for a brand-new thread MUST be skipped only while the preferred account is in overload backoff and the configured strategy selects an overload-free candidate; when no such candidate is selectable the preference MUST be honored. The service MUST emit an internal `sticky_owner_overload_isolation_reroute` diagnostic for each release without adding it to the stable failure taxonomy; the diagnostic MUST NOT include account identifiers.

#### Scenario: Isolated owner is released to an overload-free sibling

- **GIVEN** a `prompt_cache` session pinned to account A, which is isolated for overload
- **AND** account B is selectable and not in overload backoff
- **WHEN** the next request on that session selects an account
- **THEN** account B is selected and the mapping is rebound to B
- **AND** the probe reservation pool is the overload-free pool the pick came from

#### Scenario: Soft backoff keeps the warm owner

- **GIVEN** a session pinned to account A, which is in soft overload backoff below the isolation level
- **WHEN** the next request selects an account
- **THEN** account A keeps the session

#### Scenario: Isolated owner is kept when nothing else is selectable

- **GIVEN** a session pinned to isolated account A whose only sibling is rate-limited
- **WHEN** the next request selects an account
- **THEN** account A keeps the session rather than failing the request

#### Scenario: Isolated owner is not released to a saturated sibling

- **GIVEN** a bare `codex_session` mapping pinned to isolated account A with cap spillover disabled
- **AND** the only sibling B is at its stream cap
- **WHEN** the next request selects an account with a stream lease
- **THEN** account A serves the request (its cap exemption is kept) and no `account_stream_cap` error is returned

#### Scenario: Capped and isolated owner is rebound to the spillover target

- **GIVEN** a bare `codex_session` mapping pinned to account A, which is at its stream cap with spillover enabled and is isolated
- **WHEN** the next request spills to sibling B
- **THEN** the mapping is rebound to B (a capped-but-not-isolated owner keeps the request-local spillover and preserves the mapping)

### Requirement: Direct HTTP stream continuity conflicts surface the conflict code

When a direct HTTP (SSE) stream fails closed because required continuity-owner
selection reported `continuity_owner_conflict`, the emitted `response.failed`
error envelope MUST carry the `continuity_owner_conflict` error code and the
selection's conflict message rather than the generic
`previous_response_owner_unavailable` code. The continuity fail-closed
telemetry for that failure MUST record surface `http_stream` with reason
`owner_conflict` and MUST propagate the selection error code as the upstream
error code, and the persisted request log MUST record the surfaced conflict
code. A preferred-owner selection failure without a conflict code MUST keep
the existing `previous_response_owner_unavailable` envelope, the
`owner_account_unavailable` telemetry reason, and the existing upstream error
codes.

#### Scenario: Conflicting continuity owners on a direct stream

- **GIVEN** a direct HTTP stream request whose required continuity-owner selection fails with `continuity_owner_conflict`
- **WHEN** the stream fails closed without a selected account
- **THEN** the SSE `response.failed` event carries error code `continuity_owner_conflict` and the selection's conflict message
- **AND** continuity fail-closed telemetry records surface `http_stream`, reason `owner_conflict`, and upstream error code `continuity_owner_conflict`
- **AND** the persisted request log records error code `continuity_owner_conflict`

#### Scenario: Owner unavailability without a conflict is unchanged

- **GIVEN** a direct HTTP stream request whose preferred continuity owner cannot be selected
- **AND** selection did not report `continuity_owner_conflict`
- **WHEN** the stream fails closed without a selected account
- **THEN** the SSE `response.failed` event carries `previous_response_owner_unavailable`
- **AND** continuity fail-closed telemetry records reason `owner_account_unavailable` with the existing upstream error codes

### Requirement: Required continuity-owner selection failures are explicit

Account selection MUST distinguish a proven request continuity owner from an ordinary preferred account or configured routing restriction. Durable continuity provenance, including a soft prompt-cache follow-up whose durable record contains a latest turn state, MUST make the preferred account mandatory for local reuse and fresh selection. When a required continuity owner cannot be returned because that owner no longer exists or is unavailable after supported selection reloads, selection MUST return `continuity_owner_unavailable`. When the owner exists but is outside the effective model, API-key, security, authorization, or routing policy, selection MUST return `continuity_owner_policy_conflict` or the more specific existing policy code. Stable local capacity codes MUST remain unchanged. The HTTP bridge MUST translate only a typed `continuity_owner_unavailable` selection result for its required owner to `previous_response_owner_unavailable`.

#### Scenario: Required owner is unavailable while another account is healthy

- **GIVEN** a follow-up has proven account A as its continuity owner
- **AND** account B remains available for unrelated traffic
- **WHEN** account A cannot be selected after supported selection reloads
- **THEN** selection returns `continuity_owner_unavailable`
- **AND** the HTTP bridge returns `previous_response_owner_unavailable` unless verified replay applies
- **AND** it does not select account B as the unchanged continuation owner

#### Scenario: Required owner conflicts with selection policy

- **GIVEN** a follow-up has proven account A as its continuity owner
- **WHEN** account A is outside the request's model, API-key, security, authorization, or routing policy
- **THEN** selection returns `continuity_owner_policy_conflict` or the existing more-specific policy code
- **AND** the bridge does not convert that result into replay permission

#### Scenario: Soft prompt-cache follow-up has durable continuity provenance

- **GIVEN** a soft prompt-cache follow-up resolves durable account A and a latest turn state
- **AND** a stale local lane for the same prompt-cache key uses account B
- **WHEN** the bridge reuses or creates the follow-up lane
- **THEN** it rejects the stale account-B lane and requires account A
- **AND** account selection cannot fall back to B before verified replay proof applies

#### Scenario: Soft prompt-cache first turn changes model

- **GIVEN** a first-turn request reuses a soft prompt-cache key whose durable row was written for another model
- **AND** the request carries no previous-response, turn-state, or session-header continuation
- **WHEN** the bridge isolates the incompatible durable lane
- **THEN** it does not make the stale durable account a required continuity owner
- **AND** ordinary first-turn account selection remains available

#### Scenario: Required owner reaches a local capacity cap

- **WHEN** a required owner reaches `account_stream_cap` or `account_response_create_cap`
- **THEN** selection preserves that code and its `429` classification
- **AND** bounded local-cap recovery remains available

#### Scenario: Failure occurs after the owner was selected

- **GIVEN** the required continuity owner was selected successfully
- **WHEN** token refresh, authentication, connection, timeout, or another transport step fails
- **THEN** the failure retains its ordinary authentication or upstream classification
- **AND** it is not rewritten to `previous_response_owner_unavailable`

### Requirement: Restricted ownership misses do not change global health

A terminal no-account result MUST change global degraded state only when the attempted selection represents the effective global routing pool. A request explicitly constrained by continuity ownership, or a resolved hard sticky owner, MUST leave the existing global health state unchanged when that restricted owner is unavailable; the restricted miss MUST neither enter nor clear degraded mode. A configured single-account routing strategy without request ownership provenance remains an effective global routing policy and MUST retain the existing degraded-state behavior when its configured pool is unavailable.

#### Scenario: Explicit owner restriction misses

- **GIVEN** a request is explicitly constrained to account A by continuity ownership
- **AND** another account remains healthy in the wider pool
- **WHEN** account A is unavailable
- **THEN** the request receives its restricted-owner failure
- **AND** the service does not enter global degraded mode

#### Scenario: Resolved hard sticky owner misses

- **GIVEN** a hard sticky mapping resolves account A as the request owner
- **WHEN** account A is unavailable
- **THEN** the request fails closed without rerouting
- **AND** the restricted miss does not mark the wider process globally degraded

#### Scenario: Configured single-account pool is unavailable

- **GIVEN** the operator configured single-account routing without a request ownership constraint
- **WHEN** that effective routing pool has no available account
- **THEN** the service retains its existing global degraded-mode behavior

#### Scenario: Unrestricted pool is unavailable

- **WHEN** an unrestricted selection finds no available upstream account
- **THEN** the service enters degraded mode and retains the existing global exhaustion envelope

### Requirement: Owner-bound requests retry the same owner after an upstream burst rejection

When a pre-visible upstream failure on the HTTP stream transport is a code-less HTTP 429 (classified `retryable_transient` with HTTP status 429) and the request is **owner-bound** — it cannot be dispatched to another account because its payload replay is bound to the failed account (input items that are not account-neutral, such as `reasoning`), because a required preferred account, a file-pin owner, or a turn-state owner names that account, or because single-account routing is configured — the failover decision MUST be `retry_same_account` while a same-account retry is available and `surface` otherwise; it MUST NOT be `failover_next`, since an owner-bound request can never cross accounts. A same-account retry is available only while fewer than 3 same-account burst retries have been made for that account within the request, the request's remaining startup budget is positive, and no downstream-visible output has been emitted. For each `retry_same_account` the proxy MUST keep the account selectable for the request (no exclusion, replay binding kept), engage the `account-routing` burst cooldown for the account immediately (replica-local runtime state only, so other requests are steered away during the burst whether or not the stream is keyed), and wait `min(10 s, max(retry_after, 1 s * 2^(n-1)))` for retry `n` (1 s, 2 s, 4 s without an upstream `Retry-After`; the upstream value is a floor) through the propagated startup wait, so the HTTP response status and headers are not committed during the wait; on a route that propagates HTTP errors the wait MUST NOT emit keepalive frames (a frame would commit a 200/SSE response), and the startup probe MUST keep holding the headers across consecutive waits. The wait MUST be bounded by the remaining request budget and MUST use the stream scheduler and clock seams rather than raw sleeps. A same-account retry MUST NOT record a transient error penalty against the owner: the penalty is written exactly once, when the failure is finally surfaced, so a bursting owner never enters selection error backoff and a successful retry is never followed by a deferred penalty. On the pre-visible path the stream lease is kept across the retry (the redispatch stays inside the admitted slot); on the post-refresh path the lease is released before the same owner is re-selected. When the retry count is exhausted or the budget is spent, the proxy MUST surface the original upstream 429 status and body, and the surfaced response MUST carry a `Retry-After` header equal to the upstream value when present and `5` otherwise. The `Failover decision` log line MUST report the action that is actually executed. A request that is not owner-bound MUST keep the existing `failover_next` handling. The same rule MUST apply to the failover decision taken after a 401 token refresh.

#### Scenario: Same owner is retried after a bounded backoff while headers are held

- **GIVEN** a `prompt_cache` session pinned to account A whose payload carries `reasoning` items
- **AND** upstream answers the first dispatch with a code-less HTTP 429 and no `Retry-After`
- **WHEN** the proxy handles the failure before any downstream-visible output
- **THEN** the failover decision is `retry_same_account`
- **AND** the proxy waits 1 second without committing the HTTP response status
- **AND** the request is dispatched again to account A and, when upstream now accepts it, the client receives the normal response

#### Scenario: Retries are bounded and the original 429 is surfaced with Retry-After

- **GIVEN** an owner-bound request from a native Codex client (HTTP errors propagate, no OpenAI SDK contract) to account A
- **AND** upstream answers four consecutive dispatches with a code-less HTTP 429 and no `Retry-After`
- **WHEN** the proxy handles the fourth rejection
- **THEN** the proxy has waited 1, 2, and 4 seconds between dispatches without committing the HTTP response
- **AND** the client receives HTTP 429 with the original upstream body and `Retry-After: 5`
- **AND** the `Failover decision` log reports `retry_same_account` three times and then `surface`
- **AND** exactly one transient error penalty is recorded for account A

#### Scenario: Same-account retry leaves no penalty on a successful owner

- **GIVEN** an owner-bound request on an API key with a usage reservation
- **AND** upstream answers the first dispatch with a code-less HTTP 429 and the retry succeeds
- **WHEN** the stream completes
- **THEN** the burst cooldown for account A was engaged at rejection time, before the backoff wait
- **AND** no transient error penalty is recorded for account A after the stream's success
- **AND** the cooldown deadline is not extended when the stream settles

#### Scenario: Single-account routing retries the selected account

- **GIVEN** single-account routing is configured for account A and an account-neutral payload
- **WHEN** account A returns a code-less HTTP 429 before any downstream-visible output
- **THEN** the failover decision is `retry_same_account`, never `failover_next`
- **AND** account A is dispatched again after the backoff

#### Scenario: Post-refresh burst rejection retries the same owner

- **GIVEN** an owner-bound request whose first dispatch fails with HTTP 401 and the token refresh succeeds
- **AND** the redispatch returns a code-less HTTP 429
- **WHEN** the proxy handles the post-refresh failure
- **THEN** the `Failover decision` log reports `phase=post_refresh` and `retry_same_account`
- **AND** the same owner is re-selected and dispatched after the backoff
- **AND** if the owner cannot be re-selected, the surfaced 429 carries `Retry-After`

#### Scenario: Upstream Retry-After floors the wait and the budget bounds it

- **GIVEN** an owner-bound request whose first rejection carries `Retry-After: 3`
- **WHEN** the same-account retries are scheduled
- **THEN** the waits are 3, 3, and 4 seconds, each no longer than the request's remaining startup budget
- **AND** the surfaced 429, if retries are exhausted, carries `Retry-After: 3`
- **AND** a request whose remaining budget is exhausted surfaces the original 429 without further retries

#### Scenario: No cross-account reroute for an owner-bound request

- **GIVEN** an owner-bound request to account A and a selectable sibling B
- **WHEN** account A returns a code-less HTTP 429 and retries are exhausted
- **THEN** no dispatch is made to account B
- **AND** the replay binding to account A is preserved throughout
- **AND** the `Failover decision` log never reports `failover_next` for this request

#### Scenario: Downstream-visible output is never replayed

- **GIVEN** an owner-bound request that has already emitted a downstream-visible event
- **WHEN** upstream then fails the stream with HTTP 429
- **THEN** the failover decision is `surface`
- **AND** no same-account retry is attempted

#### Scenario: Movable request keeps failing over

- **GIVEN** a request whose payload is account-neutral and that carries no owner-binding continuity source
- **WHEN** account A returns a code-less HTTP 429 before any downstream-visible output
- **THEN** the failover decision is `failover_next`
- **AND** the next selection excludes account A and, while another candidate exists, the burst cooldown steers it away from A as well

