# proxy-admission-control Delta

## ADDED Requirements

### Requirement: Quota-consuming subscription work enforces an API key's estimated usage allocation

For an API key with `usage_share_percent`, Codex-LB SHALL build an estimate in the existing API-key policy snapshot over the key's assigned account pool, or the eligible global account pool when unscoped. For each account with complete fresh long-window evidence, the system SHALL multiply that account's normalized used credits by the API key's fraction of tracked request-demand units on that account from the current long-window start through that evidence sample's recording time. The estimated key usage SHALL be the sum of those attributed credits. The allowance SHALL be `usage_share_percent / 100` multiplied by the pool's normalized long-window capacity.

Quota-consuming subscription work SHALL be denied with stable code `api_key_usage_share_limit_reached` when estimated usage is greater than or equal to the allowance. HTTP surfaces SHALL return 429; an already-accepted direct WebSocket SHALL emit the equivalent per-request terminal error without closing unrelated work. HTTP requests SHALL receive the estimate through the existing 60-second API-key policy cache, while direct WebSocket requests SHALL rebuild the policy snapshot for each new client turn. Subscription-backed Responses and Chat Completions (including Images work routed through Responses), compact, direct WebSocket response creation, and transcription SHALL enforce the policy. Fixed token/USD rules and opportunistic admission SHALL remain independent.

Demand attribution SHALL use the existing quota-planner demand-unit formula as a relative weight. An account-bound request-log row SHALL count as subscription-generation demand when it carries an ordinary nonempty generation model; the empty model used by metadata/control, thread-goal, and realtime rows and the `files-create` / `files-finalize` sentinel models SHALL be excluded. Raw model-source rows SHALL also be excluded. A request refused locally before upstream dispatch MUST remain account-neutral and MUST NOT enter either side of later usage-share attribution. Internal warm-up SHALL remain in the denominator but MUST NOT enter an API key numerator even when the probe carries that key. Unkeyed generation SHALL remain unattributed. An account with no tracked generation demand SHALL attribute zero of its current used credits.

#### Scenario: Other keys do not block an unused key

- **GIVEN** a pool has consumed 80 percent of its quota
- **AND** Key A has a 20 percent allocation and no tracked demand in the current account windows
- **WHEN** Key A starts subscription-backed generation work
- **THEN** its estimated usage is zero
- **AND** the request is not denied by the usage-share policy

#### Scenario: Later demand cannot inherit earlier usage

- **GIVEN** an account's contributing usage sample was recorded at time T
- **AND** an API key produces new demand on that account after T
- **WHEN** the key's estimate is rebuilt before a newer usage sample exists
- **THEN** post-T demand contributes to neither the numerator nor the denominator for that sample
- **AND** the key cannot be charged a share of usage observed before its demand occurred

#### Scenario: Proportional demand reaches the allocation

- **GIVEN** a pool has 300 normalized credits and currently shows 90 credits consumed
- **AND** a key produced 20 percent of tracked demand on each contributing account
- **AND** the key's configured allocation is 6 percent
- **WHEN** the request reaches usage-share admission
- **THEN** its estimated usage and allowance are both 18 credits
- **AND** the request is denied with `api_key_usage_share_limit_reached`

#### Scenario: Snapshot expires after authentication but before admission

- **GIVEN** authentication produced a complete usage-share snapshot
- **AND** its earliest reset, evidence-freshness, or routing boundary elapses before subscription admission runs
- **WHEN** the request reaches usage-share admission
- **THEN** the stale snapshot does not deny the request
- **AND** the next policy load rebuilds the estimate through the existing cache boundary

#### Scenario: Cached estimate is rebuilt at its reset boundary

- **GIVEN** HTTP authentication cached a usage-share policy snapshot whose earliest account reset has elapsed
- **WHEN** the key authenticates again before the cache's ordinary 60-second TTL expires
- **THEN** the stale cache entry is discarded
- **AND** the usage-share policy snapshot is rebuilt before request admission

#### Scenario: Cached estimate expires with its evidence

- **GIVEN** HTTP authentication cached a complete usage-share policy snapshot
- **AND** its earliest contributing usage sample reaches the shared freshness horizon before the cache's ordinary TTL or any quota reset
- **WHEN** the key authenticates again
- **THEN** the cached snapshot is discarded and rebuilt
- **AND** stale evidence cannot keep denying the key from cache

#### Scenario: Account reset restarts attribution without a ledger

- **GIVEN** an account's long quota window resets and its next usage sample belongs to the new window
- **WHEN** the key's policy snapshot is rebuilt
- **THEN** only request demand from the new `reset_at - window_minutes` boundary through the new sample's recording time contributes
- **AND** no attribution-epoch or snapshot-delta ledger is required

#### Scenario: Pool membership changes resize the allocation

- **WHEN** an eligible account is assigned, unassigned, added, removed, paused, resumed, or changes plan
- **THEN** cached HTTP policy snapshots affected by the mutation are evicted locally and on peer replicas
- **AND** the next API-key policy snapshot uses the resulting account pool and normalized capacity
- **AND** only demand from accounts in that pool contributes to estimated usage

#### Scenario: Reauthentication transition refreshes the routing-expiry boundary

- **GIVEN** a cached policy snapshot was built while an account was `active`
- **WHEN** a permanent credential failure moves that account to `reauth_required`
- **THEN** local and peer API-key policy snapshots are evicted through the existing account-routing invalidation
- **AND** the rebuilt snapshot keeps the account routable only through its known stored access-token expiry

#### Scenario: Known-expired reauthentication accounts leave the pool

- **GIVEN** an account is in `reauth_required` status
- **WHEN** its stored access token has a known expiry at or before the policy snapshot time
- **THEN** the account contributes neither capacity nor demand to the usage-share estimate
- **AND** it does not make the estimate unavailable merely because its usage evidence is absent
- **AND** a reauthentication account whose access-token expiry is unknown remains in the pool, matching request routing

#### Scenario: Non-subscription work bypasses the allocation

- **WHEN** a request is served by a configured model source, lists models, performs metadata/control or thread-goal work, uses file-control operations, or carries realtime traffic
- **THEN** it does not pass through usage-share admission
- **AND** its request demand contributes to neither side of later usage-share attribution
- **AND** it is not denied by this policy

#### Scenario: Reattach inherits prior admission

- **GIVEN** a WebSocket response was already admitted
- **WHEN** the client reattaches to it
- **THEN** usage-share admission is not evaluated again

#### Scenario: Retry does not duplicate the decision

- **GIVEN** subscription work was admitted with an `ApiKeyData` policy snapshot
- **WHEN** bounded failover or transparent replay revisits routing
- **THEN** it reuses that admission instead of evaluating the share again
- **AND** it creates no additional usage attribution or reservation

#### Scenario: Reused direct WebSocket still admits every new turn

- **GIVEN** a direct WebSocket already has an open subscription upstream
- **WHEN** the client submits another fresh `response.create`
- **THEN** the server rebuilds the key policy snapshot for that client turn
- **AND** it enforces usage share before reusing the upstream socket

#### Scenario: HTTP bridge origin performs the subscription decision

- **GIVEN** a public origin is about to forward subscription work to an HTTP-bridge owner
- **WHEN** the origin completes source routing and provisional fixed-limit reservation
- **THEN** the origin enforces the cached usage-share policy before forwarding
- **AND** the owner does not repeat the decision for the signed forwarded request

#### Scenario: Local share refusal releases a provisional fixed-limit reservation

- **GIVEN** a route provisionally reserved a fixed token or dollar limit before usage-share admission
- **WHEN** usage-share admission refuses the request before upstream dispatch
- **THEN** the existing local pre-dispatch cleanup releases the provisional reservation
- **AND** no upstream request is created

#### Scenario: Reused-socket refusal does not feed its own estimate

- **GIVEN** a direct WebSocket already has an open subscription account
- **WHEN** a later fresh turn is refused locally by usage-share admission before dispatch
- **THEN** the refusal log is not attributed to the open account
- **AND** the refused turn contributes to neither side of later usage-share attribution
