## MODIFIED Requirements

### Requirement: Sticky sessions are explicitly typed
The system SHALL persist each sticky-session mapping with an explicit kind so durable Codex backend affinity, durable dashboard sticky-thread routing, and bounded prompt-cache affinity can be managed independently. Budget-pressure reallocation MUST apply only to mappings whose kind/source is soft. A raw or legacy `codex_session` mapping MUST remain owner-bound because it may represent explicit turn-state continuity; budget pressure MUST NOT delete or rebind it.

An explicit Codex goal-continuation restart MAY abandon a raw legacy `codex_session` owner only when the complete Responses payload is account-neutral and self-contained: it MUST have no nonblank `previous_response_id`, no nonblank `conversation`, no account-scoped input file or image reference, and no unresolved or orphan tool state. Classification MUST use the canonical upstream request form so accepted compatibility controls and transport-envelope fields do not make equivalent requests disagree. The owner MUST be persisted as `PAUSED`, `RATE_LIMITED`, or `QUOTA_EXCEEDED` and MUST belong to the authenticated request's account-assignment and security-policy scope computed before model and service-tier eligibility; local capacity, model eligibility, retry exclusions, runtime health, budget pressure, and an out-of-scope owner MUST NOT determine mutation authority. The retirement write MUST compare the current mapping owner and unavailable account status atomically, MUST preserve a concurrently changed mapping or recovered owner, and on success MUST let normal selection establish affinity to the replacement account. Because a raw key's persisted source is ambiguous, goal-restart abandonment MUST apply only to `session_header` interpretation and MUST retain the stored account as hard ownership for an explicit `turn_state` lookup using the same text. During a rolling deployment or rollback, replicas that do not understand source-qualified abandonment MUST continue treating that retained account as hard ownership. A selector that observes source-qualified abandonment initially or after losing the retirement compare-and-set MUST exclude the retained retired owner until replacement affinity is persisted, even if its account inputs predate retirement. Restart authority MUST remain scoped to the classified request and MUST NOT persist on a reusable bridge for later requests. A live or durable HTTP bridge for the same process session MUST NOT bypass this guarded selection through local reuse, owner forwarding, or preferred-owner promotion. Canonical replacement MUST preserve an already reserved predecessor request's authority to submit on its detached draining generation after queue publication clears the mutable reservation marker. A detached predecessor MAY finish its admitted response but MUST NOT publish new turn-state or previous-response aliases under the replacement generation's canonical key. Every detached generation, including an idle generation already marked closed for admission, MUST remain owned by the bridge lifecycle, MUST count against the configured session cap until resource closure completes, and MUST be closed during service shutdown, account invalidation, or drained reservation cleanup. The admission-only closed state MUST NOT be treated as proof that the socket and leases have a close owner. Resource teardown MUST be single-flight, and all close paths MUST release detached-generation ownership only after resource closure finishes, even when a close caller is cancelled. Shutdown MUST schedule and await every snapshotted generation before propagating cancellation. A new local bridge generation that replaces durable ownership under the same replica identity MUST advance the durable owner epoch before serving requests so a predecessor's late release cannot close the replacement lease, including when model-transition isolation discards the durable lookup as a routing input.

A later security-authorized bridge replacement that revalidates a raw legacy row MUST preserve the request's typed continuity source. A source-scoped session-header abandonment MUST remain ownerless for that replacement while an explicit turn-state lookup of the same raw value remains owner-bound. A planned capacity eviction MUST NOT stop counting a detached generation merely because its bounded close wait timed out. Shutdown MUST retain any generation whose resource close fails so a later shutdown pass can retry finalization. Drain status MUST count pending or queued work on a detached generation even after it is closed for admission. When a verified restart replaces an idle predecessor that fills the configured session cap, admission MUST give that predecessor synchronous bounded-close ownership and MUST recheck actual lifecycle capacity before opening the replacement.

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

#### Scenario: Generated WebSocket turn state does not follow a restarted goal

- **GIVEN** the proxy generated the downstream turn-state header used by an upstream WebSocket on account A
- **AND** a marked self-contained goal restart retires that socket and selects account B
- **WHEN** the proxy opens the replacement WebSocket
- **THEN** it removes account A's generated turn-state token before connect
- **AND** it still forwards the restart's full replay payload to account B
