# rate-limit-reset-credits Specification

## Purpose
Poll, cache, and safely redeem upstream rate-limit reset credits per account without changing account health when polling fails.
## Requirements
### Requirement: Reset credits are polled per account on a fixed cadence

The system SHALL poll upstream `GET /wham/rate-limit-reset-credits` for each eligible account on a configurable cadence that defaults to 60 seconds, using that account's stored OAuth bearer token and `chatgpt-account-id`. The scheduler SHALL start with the application lifespan when reset-credit polling is enabled. Because snapshots are kept in process-local memory, every running replica SHALL refresh its own snapshot cache instead of relying on leader election, and the scheduler SHALL NOT be leader-gated while snapshots remain process-local. Each replica SHALL apply a randomized startup delay of up to one full interval and randomized per-tick jitter of +/-10% so replica ticks are desynchronized. The aggregate upstream fetch rate scales with the number of running replicas; `rate_limit_reset_credits_refresh_interval_seconds` is the operator control for total upstream load. The configured interval SHALL schedule discovery work; bounded queue delay SHALL be measured separately from the interval. Discovery and deadline work SHALL use a bounded worker pool with independently owned database sessions and reserved capacity for known deadlines. The poll SHALL skip any account that is paused, requires reauthentication, deactivated, or lacks a usable `chatgpt-account-id`.

When dashboard setting `auto_redeem_reset_credits_before_expiry` is enabled, the system SHALL schedule the soonest-expiring available credit for automatic redemption when it has more than zero and at most 60 minutes remaining. Known deadlines SHALL execute independently of completion of the bulk discovery scan. Automatic redemption SHALL reuse the common redemption helper, cross-replica serialization, durable ledger, invalidation, and usage-refresh path. Immediately before consuming, it SHALL re-read the setting and target account and abort if disabled or if the account is missing, pending deletion, paused, requires reauthentication, deactivated, or lacks a usable `chatgpt-account-id`. It SHALL constrain the helper to the exact triggering credit id and expiry and SHALL abort if the fresh pre-consume fetch no longer reports that same credit and expiry as available. It SHALL NOT substitute another credit for a disappeared or expired target.

Automatic requests SHALL have a stable identity per account, credit id, and expiry. Existing pins for that credit SHALL be reconciled before a new request is created. A pin alone SHALL NOT be treated as success. A confirmed reset SHALL suppress further consumes for that request. Recovery of a retryable or unknown automatic attempt SHALL retain the same request id and credit and SHALL require a fresh available target before its expiry. Rejection of a stale cache write SHALL NOT discard the target's deadline reconciliation.

#### Scenario: Default cadence polls every 60 seconds
- **WHEN** the application starts with default settings
- **THEN** discovery is scheduled on the 60-second interval with the jitter bound, without overlapping discovery requests for the same account

#### Scenario: Every replica refreshes its local cache
- **WHEN** the application is deployed with multiple running replicas
- **THEN** each replica refreshes its own in-memory reset-credit snapshots on the configured cadence
- **AND** dashboard reads served by any replica can observe populated reset-credit data after that replica's refresh tick

#### Scenario: Two replicas do not fetch in lockstep
- **GIVEN** two replicas start with identical configuration
- **WHEN** their refresh loops run
- **THEN** their startup delays are independent uniform draws over the full interval and each tick interval carries independent +/-10% jitter, so the replicas' tick times are not synchronized

#### Scenario: Ineligible accounts are skipped
- **WHEN** an account is persisted as `paused`, `reauth_required`, or `deactivated`
- **THEN** the scheduler performs no upstream reset-credits fetch for that account
- **AND** the cached snapshot for that account (if any) is left untouched by the skip

#### Scenario: Automatic redemption is disabled by default
- **WHEN** the dashboard settings row is created for the first time
- **THEN** `auto_redeem_reset_credits_before_expiry` is `false`
- **AND** the reset-credit refresh scheduler only refreshes snapshots and does not redeem credits automatically

#### Scenario: Automatic redemption reuses the existing redeem path
- **GIVEN** `auto_redeem_reset_credits_before_expiry` is enabled
- **AND** a refreshed eligible account snapshot includes an available credit whose expiry is within the automatic redemption window
- **WHEN** the reset-credit refresh loop processes that account
- **THEN** the system redeems the soonest-expiring available credit through the same redemption function used by the dashboard consume endpoint
- **AND** the redemption uses the existing per-account serializer, durable idempotency ledger, cache invalidation, and usage refresh behavior
- **AND** duplicate automatic attempts for an already confirmed request do not issue another upstream consume, while unresolved pins follow same-credit recovery

#### Scenario: Automatic redemption ignores non-expiring snapshots
- **GIVEN** `auto_redeem_reset_credits_before_expiry` is enabled
- **AND** a refreshed eligible account snapshot has no available credit with `expires_at`
- **WHEN** the reset-credit refresh loop processes that account
- **THEN** the system does not attempt automatic redemption for that snapshot

#### Scenario: Automatic redemption waits until the one-hour expiry window
- **GIVEN** `auto_redeem_reset_credits_before_expiry` is enabled
- **AND** a refreshed eligible account snapshot's soonest available credit expires more than 60 minutes in the future
- **WHEN** the reset-credit refresh loop processes that account
- **THEN** the system refreshes the snapshot but does not attempt automatic redemption

#### Scenario: Due credit bypasses the bulk discovery backlog
- **GIVEN** a known credit enters the one-hour window while a bulk scan is processing other accounts
- **WHEN** its scheduled deadline work becomes ready
- **THEN** reserved deadline capacity processes it without waiting for the full scan to finish
- **AND** the common helper revalidates the same credit before consume

#### Scenario: Cache generation conflict preserves deadline work
- **GIVEN** a fetch observes an available credit inside the automatic window
- **AND** cache invalidation prevents publishing that response
- **WHEN** the scheduler handles the rejected write
- **THEN** it retains or enqueues exact-credit reconciliation
- **AND** a fresh serialized check determines whether consume is still allowed

#### Scenario: Missing target never falls through to a later credit
- **GIVEN** automatic work targets C1 and a later credit C2 is available
- **WHEN** the pre-consume fetch no longer reports C1 with its original expiry as available
- **THEN** no consume is sent for C2 as part of that attempt

#### Scenario: Disabling the setting stops queued consumes
- **GIVEN** automatic work is queued while the setting is enabled
- **WHEN** the operator disables it before execution
- **THEN** queued work performs no upstream consume

#### Scenario: Partial failures respect task and session ownership
- **GIVEN** several accounts are being refreshed concurrently
- **WHEN** one fetch fails or the scheduler stops
- **THEN** concurrency remains bounded and no database session is used concurrently by multiple tasks
- **AND** shutdown cancels and awaits all scheduler-owned workers

### Requirement: Reset credit snapshots are cached in memory keyed by account

The system SHALL store the most recent successful reset-credits response per account in an in-memory store keyed by account id. The store SHALL be concurrency-safe and SHALL provide an `invalidate(account_id)` operation. Account-summary mappers SHALL join the cached snapshot onto each account summary, exposing `available_reset_credits` (integer) and `reset_credit_nearest_expires_at` (ISO timestamp or null). Account summaries SHALL additionally expose nullable `reset_credit_fetched_at`, populated from the successful snapshot fetch time and null when no snapshot exists. Accounts with no cached snapshot SHALL expose `available_reset_credits: 0` and `reset_credit_nearest_expires_at: null`.

#### Scenario: Account summary reflects cached credits
- **GIVEN** an account has a cached reset-credits snapshot with `available_count: 2` and a soonest expiry of `2026-07-10T00:00:00Z`
- **WHEN** the account-summary mapper builds the summary for that account
- **THEN** the summary exposes `available_reset_credits: 2` and `reset_credit_nearest_expires_at: "2026-07-10T00:00:00Z"`

#### Scenario: Missing cache presents as zero credits
- **GIVEN** an account has no cached reset-credits snapshot (e.g. immediately after restart)
- **WHEN** the account-summary mapper builds the summary for that account
- **THEN** the summary exposes `available_reset_credits: 0` and `reset_credit_nearest_expires_at: null`

#### Scenario: Invalidate forces re-fetch on next tick
- **WHEN** a caller invokes `invalidate(account_id)` for an account
- **THEN** subsequent reads for that account return no cached snapshot
- **AND** the next scheduler tick fetches a fresh snapshot from upstream

#### Scenario: In-flight refresh cannot restore an invalidated snapshot
- **GIVEN** a scheduler refresh starts fetching reset credits for an account
- **AND** another caller invokes `invalidate(account_id)` before that refresh stores its fetched response
- **WHEN** the refresh completes
- **THEN** the stale fetched response MUST NOT be written back into the cache

#### Scenario: Dashboard read invalidates stale snapshots for ineligible accounts
- **GIVEN** an account has a cached reset-credits snapshot
- **AND** the account is now persisted as `paused`, `reauth_required`, `deactivated`, or no longer has a usable `chatgpt-account-id`
- **WHEN** the dashboard invokes `GET /api/accounts/{id}/rate-limit-reset-credits`
- **THEN** the endpoint returns `null` without calling upstream
- **AND** the cached snapshot for that account is invalidated

#### Scenario: Fresh zero is distinguishable from missing cache
- **WHEN** an account summary is read after a successful fetch of zero available credits
- **THEN** its count is zero and `reset_credit_fetched_at` is populated
- **AND** a summary with no snapshot has a null freshness timestamp

### Requirement: Operators can redeem the soonest-expiring available credit

The system SHALL expose a dashboard endpoint `POST /api/accounts/{account_id}/rate-limit-reset-credits/consume` that redeems exactly one credit for the named account. The endpoint SHALL select, from the freshest cached snapshot, the credit whose `status` is `available` with the smallest `expires_at`, generate a `redeem_request_id` (UUID v4), and forward `{credit_id, redeem_request_id}` to upstream `POST /wham/rate-limit-reset-credits/consume` using the account's bearer token and `chatgpt-account-id`. Before forwarding the consume, the endpoint SHALL durably record the selected `credit_id` against the request's `redeem_request_id` in the shared database; a retry carrying the same `redeem_request_id` MUST reuse that recorded `credit_id` even when served by a different replica. A cached snapshot with `available_count <= 0` MUST be treated as having no redeemable credits, even if the cached `credits` list contains an item marked `available`. When the fresh pre-consume fetch reports `available_count <= 0` or no available credit items, the endpoint SHALL replace any prior cached snapshot for that account with the fresh upstream snapshot before returning a conflict. This SHALL hold even when the caller supplies a `redeem_request_id` for which no durable ledger pin exists: absent a durable pin there is no proof the request is an idempotent retry, so the fresh empty fetch is authoritative and the endpoint MUST NOT pin and consume a stale cached credit. Only a pre-existing durable pin (`(account_id, redeem_request_id) -> credit_id`) authorizes forwarding that pinned credit to upstream when the fresh fetch shows no currently-available credit. On a parsed upstream response the endpoint SHALL return the existing `{code, windows_reset, redeemed_at}` fields without fabricating reset success. It SHALL persist the classified outcome and invalidate or reconcile only the target account as needed. It SHALL decrement an available-credit hint only after confirmed consumption, and SHALL NOT treat a pin, HTTP 200 alone, or missing credit as a confirmed reset. A confirmed result SHALL be persisted before usage refresh; failure of that refresh SHALL NOT cause the credit to be consumed again. Automatic redemption SHALL execute multiple independent account consumes concurrently within a fixed bound, and SHALL hand quota verification to separately reserved capacity so slow usage refresh cannot block pending consumes. The endpoint SHALL require dashboard write access; read-only guests MUST be refused.

#### Scenario: Consume selects the soonest-expiring credit
- **GIVEN** an account has cached credits with expiries `2026-07-10Z` and `2026-06-20Z`, both `status: available`
- **WHEN** the operator invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the request forwarded to upstream carries the `credit_id` whose `expires_at` is `2026-06-20Z`

#### Scenario: Successful consume invalidates the cache
- **GIVEN** the operator invokes consume for an account with at least one available credit
- **WHEN** upstream returns `200` with `{code: "reset", windows_reset: 1, credit: {...}}`
- **THEN** the cached snapshot for that account is invalidated
- **AND** the response returned to the dashboard is `{code, windows_reset, redeemed_at}` derived from the upstream response

#### Scenario: Concurrent consume requests for one account are serialized
- **GIVEN** two operators invoke `POST /api/accounts/{id}/rate-limit-reset-credits/consume` at nearly the same time for the same account, whether both requests reach one process or different processes/replicas sharing the database (on both PostgreSQL and SQLite)
- **WHEN** the first request is still redeeming a credit
- **THEN** the second request MUST wait for the first request to finish before re-reading that account's cached snapshot
- **AND** the same cached `credit_id` MUST NOT be sent to upstream twice by those concurrent requests

#### Scenario: Same-redeem-request retry on another replica reuses the recorded credit
- **GIVEN** a consume with `redeem_request_id` R recorded `credit_id` C durably and forwarded the consume, but the client response was lost
- **WHEN** the retry with the same R is served by a different replica
- **THEN** that replica returns an already persisted confirmed receipt, or replays C with the same request id if the result remains unresolved, without selecting a new credit

#### Scenario: No-body consume synthesizes a redeem_request_id and pins the ledger
- **GIVEN** a dashboard consume request that carries no `redeem_request_id` (the still-supported no-body path)
- **WHEN** the endpoint selects an available credit to redeem
- **THEN** the endpoint synthesizes a UUID v4 `redeem_request_id`, durably pins the selected `credit_id` to it before the upstream consume, and forwards that recorded id to upstream
- **AND** the consume never forwards an unrecorded `redeem_request_id`

#### Scenario: Upstream consume failures surface as dashboard errors
- **GIVEN** an operator invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **WHEN** upstream returns `401`, `403`, or `409`
- **THEN** the dashboard endpoint returns the same client-facing status class instead of a generic `500`
- **AND** other upstream consume failures return a dashboard `503`

#### Scenario: Read-only guests cannot redeem
- **GIVEN** a dashboard session authenticated as a read-only guest
- **WHEN** the guest invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the request is refused before any upstream call is made

#### Scenario: Consume with no available credit returns a client error
- **GIVEN** an account whose cached snapshot reports `available_count: 0` (or has no snapshot)
- **WHEN** the operator invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the endpoint returns a `409` (or equivalent client-error) without calling upstream

#### Scenario: Fresh empty consume fetch replaces a stale cached snapshot
- **GIVEN** an account has a cached reset-credits snapshot showing at least one available credit
- **AND** the fresh pre-consume upstream fetch returns `available_count: 0` or no `status: available` items
- **WHEN** the operator invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the endpoint returns a `409` (or equivalent client-error)
- **AND** the cached snapshot for that account is replaced with the fresh upstream snapshot before the response is returned

#### Scenario: Retry-shaped request without a durable pin returns conflict on an empty fetch
- **GIVEN** an account has a stale cached snapshot showing at least one available credit
- **AND** the caller supplies a `redeem_request_id` for which no durable ledger pin exists
- **AND** the fresh pre-consume upstream fetch returns `available_count: 0` or no `status: available` items
- **WHEN** the operator invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the endpoint returns a `409` without calling upstream consume
- **AND** no ledger pin is written for that `redeem_request_id`
- **AND** the cached snapshot is replaced with the fresh (empty) upstream snapshot

### Requirement: Reset credit polling failure does not mutate account status

The reset-credits refresh scheduler SHALL NOT transition any account's persisted status (`active`, `rate_limited`, `quota_exceeded`, `paused`, `deactivated`) in response to upstream reset-credits responses. On upstream error (non-200, non-JSON, malformed 200 payload, network, or auth-like failure) the scheduler SHALL log the failure and either keep the prior cached snapshot or leave the cache unset; it SHALL NOT propagate the failure to account-status derivation.

#### Scenario: Upstream 401 on reset-credits does not deactivate the account
- **WHEN** the scheduler receives an HTTP `401` from `GET /wham/rate-limit-reset-credits` for an account
- **THEN** the account's persisted status is unchanged
- **AND** any prior cached snapshot for that account is retained

#### Scenario: Upstream 5xx retains the prior snapshot
- **GIVEN** an account has a cached snapshot from a prior successful tick
- **WHEN** the scheduler receives an HTTP `503` on the next reset-credits tick
- **THEN** the cached snapshot is retained
- **AND** the failure is logged

#### Scenario: Malformed 200 response is not cached as success
- **GIVEN** an account has a cached snapshot from a prior successful tick
- **WHEN** upstream returns HTTP `200` with a non-object body or a body missing required reset-credit fields
- **THEN** the response is treated as an upstream failure
- **AND** the cached snapshot is retained

### Requirement: Reset credit polling interval is configurable

The system SHALL expose setting `rate_limit_reset_credits_refresh_interval_seconds` (default `60`) to control the polling cadence. The system SHALL expose setting `rate_limit_reset_credits_refresh_enabled` (default `true`) to enable or disable background reset-credit polling. Because the refresh loop is the sole driver of automatic reset-credit redemption, disabling background polling SHALL also disable automatic redemption; when polling is disabled while the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is enabled, the system SHALL log a configuration-conflict warning at startup naming both settings. While polling is disabled, the dashboard settings update SHALL reject a request that newly enables `auto_redeem_reset_credits_before_expiry` with a bad-request error naming the polling toggle; an already-persisted opt-in SHALL remain readable and re-savable so unrelated settings edits are not blocked.

#### Scenario: Operator tunes the polling interval
- **GIVEN** `rate_limit_reset_credits_refresh_interval_seconds` is set to `120`
- **WHEN** the application starts and runs
- **THEN** each eligible account's credits are fetched from upstream at most once per 120 seconds

#### Scenario: Operator disables background polling
- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is set to `false`
- **WHEN** the application starts
- **THEN** the reset-credit polling scheduler does not create a background polling task
- **AND** no upstream reset-credits fetches occur

#### Scenario: Disabled polling conflicts with persisted auto-redeem opt-in
- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is set to `false`
- **AND** the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is `true`
- **WHEN** the application starts
- **THEN** the system logs a configuration-conflict warning naming both settings
- **AND** no automatic reset-credit redemption occurs while polling remains disabled

#### Scenario: Auto-redeem opt-in is rejected while polling is disabled
- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is set to `false`
- **AND** the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is `false`
- **WHEN** a dashboard settings update sets `auto_redeem_reset_credits_before_expiry` to `true`
- **THEN** the update is rejected with a bad-request error naming the polling toggle
- **AND** the persisted setting remains `false`

#### Scenario: Persisted auto-redeem does not block unrelated settings edits
- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is set to `false`
- **AND** the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is already `true`
- **WHEN** a full settings payload that keeps the opt-in unchanged is submitted
- **THEN** the update succeeds

### Requirement: Reset credit redemption is serialized and idempotent across replicas

Per-account redemption serialization MUST hold across all replicas and processes sharing one database. On PostgreSQL the system SHALL use `pg_advisory_xact_lock` keyed by the account id on the caller's session. On SQLite the system SHALL acquire a durable claim row via a single atomic conditional upsert (`INSERT ... ON CONFLICT(account_id) DO UPDATE ... WHERE expires_at < now`) with a 30-second lease, a bounded retry loop that surfaces a client-facing conflict on timeout, release on completion, and takeover of expired claims. While the redeem section runs, the claim holder SHALL renew its lease on a heartbeat cadence shorter than the lease (10 seconds) so a redemption that legitimately outlives one lease (e.g. slow upstream fetch/consume) is NOT taken over by a concurrent process; lease expiry without renewal remains the crash-recovery path. A claim-acquisition timeout SHALL surface in the caller surface's native error envelope: the dashboard error envelope on the dashboard consume endpoint and the `/v1/*` OpenAI error envelope (HTTP 409) on `POST /v1/reset-credit`. The system SHALL persist the `(account_id, redeem_request_id) -> credit_id` mapping in the shared database, committed inside the serialized section BEFORE the upstream consume call; a retry carrying the same `redeem_request_id`, served by ANY replica, MUST resolve to the originally selected `credit_id` and MUST NOT consume a different credit. Ledger rows SHALL be retained at least 24 hours, including after a failed consume. Retention SHALL apply to the account/credit group: while any pin in the group has a `created_at` within 24 hours, all its pins and canonical outcome SHALL remain available. A group with no pin inside that window SHALL be treated as absent on read and purged opportunistically BEFORE a new pin is inserted. Reusing a `redeem_request_id` after its entire prior credit group expires SHALL durably re-pin the newly selected credit instead of colliding with an expired row. Read eligibility and purge eligibility SHALL use the same group retention rule. Both the dashboard consume endpoint and `POST /v1/reset-credit` SHALL redeem inside this cross-replica serialized section. Confirmed outcome receipts SHALL short-circuit repeat consumption. Automatic recovery SHALL additionally enforce the exact-credit availability and expiry checks even when a durable pin exists. Legacy pins SHALL be classified as unknown until reconciled and SHALL NOT create an additional automatic request for the same credit during an upgrade.

#### Scenario: Retry lands on a second replica and reuses the pinned credit
- **GIVEN** replica A redeemed the soonest credit for `redeem_request_id` R but the client never saw the response
- **WHEN** the client retries the consume with the same R and the request is served by replica B
- **THEN** replica B returns the persisted confirmed receipt when present, or replays the originally pinned `credit_id` with the same request id when the result remains unresolved
- **AND** no second credit is consumed for that account

#### Scenario: Two processes on one SQLite file redeem concurrently
- **GIVEN** two processes sharing one SQLite database each receive a consume request for the same account at nearly the same time
- **WHEN** the first process holds the durable redeem claim
- **THEN** the second process waits on (or conflicts out of) the claim instead of redeeming in parallel
- **AND** at most one upstream consume is sent per selected credit

#### Scenario: Claim holder crashes and the lease recovers
- **GIVEN** a process crashed while holding the redeem claim for an account
- **WHEN** a later consume request arrives after the claim lease has expired
- **THEN** the request takes over the expired claim and proceeds without operator intervention

#### Scenario: Slow redemption keeps its claim past the original lease
- **GIVEN** a process holds the redeem claim and its redeem section (upstream fetch/consume, usage refresh) runs longer than one 30-second lease
- **WHEN** a second process attempts to acquire the claim after the original lease would have expired
- **THEN** the heartbeat-renewed lease rejects the takeover and the second process keeps waiting (or conflicts out)
- **AND** at most one upstream consume is sent per selected credit

#### Scenario: Reused redeem_request_id after TTL re-pins the new credit
- **GIVEN** an account has a ledger row for `redeem_request_id` R pinned to credit C1 whose entire account/credit group has aged past the 24h TTL
- **WHEN** a new redemption reuses R and selects a different credit C2
- **THEN** the expired row is purged before the new insert so the ledger persists `(R -> C2)`
- **AND** a same-R retry served by any replica retargets C2, not the discarded C1

#### Scenario: Expired pin is ignored on read
- **GIVEN** an account has a ledger row for `redeem_request_id` R whose entire account/credit group has aged past the 24h TTL
- **WHEN** the pin lookup for `(account_id, R)` runs before any purge write
- **THEN** the lookup returns no durable pin (the expired row reads as absent)
- **AND** the redemption re-selects against the fresh fetch and re-pins the newly selected credit rather than forwarding the stale expired `credit_id`

#### Scenario: Claim contention on the v1 surface uses the OpenAI envelope
- **GIVEN** another process holds the redeem claim for the whole acquisition timeout
- **WHEN** a client calls `POST /v1/reset-credit` for that account
- **THEN** the endpoint returns 409 in the `/v1/*` OpenAI error envelope, not the dashboard envelope

### Requirement: Reset credit snapshot invalidation propagates across replicas

After successful consumption and consume-conflict invalidation, updated writers SHALL record the affected account's durable revision and bump the shared `reset_credits` namespace coherently. Updated replicas SHALL reconcile all changed account revisions within the invalidation poll bound and invalidate only those accounts, including the originating replica. Unrelated account snapshots SHALL remain populated during normal operation. A snapshot fetched before the affected account's revision changed SHALL NOT overwrite the invalidation. Coalesced notifications SHALL retain every affected account. A failed persistence or notification operation SHALL have bounded repair, with scheduled upstream refresh as the fallback. Legacy unscoped writes or unavailable revision history SHALL use a conservative fallback during mixed-version operation; they SHALL NOT permanently suppress peer invalidation.

#### Scenario: Peer replica stops listing a redeemed credit within the poll bound
- **GIVEN** replicas A and B both cache a snapshot listing credit C as available
- **WHEN** a consume for credit C succeeds on updated replica A
- **THEN** updated replica B invalidates that account within the invalidation poll bound
- **AND** B no longer lists C from the stale snapshot

#### Scenario: Lost bump converges at the next refresh tick
- **GIVEN** the invalidation write fails after a successful consume
- **WHEN** the target account's next scheduled refresh completes
- **THEN** the peer snapshot reflects the post-redeem upstream state

#### Scenario: Redeeming one account does not clear unrelated snapshots on the source replica
- **GIVEN** replica A caches valid snapshots for account X and account Y
- **WHEN** a consume for X succeeds on A
- **THEN** A invalidates only X and retains Y's snapshot

#### Scenario: Peer invalidation preserves unrelated Reset buttons
- **GIVEN** replicas A and B cache account X and unrelated account Y
- **WHEN** X is redeemed on A and B processes the scoped revision
- **THEN** B retains Y's reset-credit count and expiry

#### Scenario: Coalesced writes retain all affected accounts
- **GIVEN** X and Y are redeemed before a peer's next invalidation poll
- **WHEN** the peer reconciles the shared version and account revisions
- **THEN** it invalidates both X and Y without invalidating unrelated Z

#### Scenario: Legacy writer remains safe during rollout
- **GIVEN** an older replica emits an unscoped invalidation during a rolling upgrade
- **WHEN** an updated replica reconciles it
- **THEN** a conservative fallback or bounded refresh removes stale redeemed-credit state
- **AND** the event does not suppress later scoped revisions

### Requirement: Daybreak capability intent cannot downgrade through reset-credit routing

`POST /v1/reset-credit` and `POST /api/codex/rate-limit-reset-credits/consume` (with or without its trailing slash) MUST require a valid proxy API key whenever `X-Codex-LB-Required-Capability` is present. After authentication they MUST return HTTP 400 with `error.code = "required_capability_transport_unsupported"` before account lookup, ChatGPT usage-identity validation, credential decryption, upstream route resolution, reset-credit fetch, or reset-credit consume. Headerless requests MUST retain their existing authentication and redemption behavior. Capability-bearing reads of `/api/codex/usage`, `/v1/usage`, and `/v1/reset-credit` MAY remain available after proxy API-key authentication because their API-key paths are local and do not select an upstream account or dispatch an upstream request. They MUST NOT enter ChatGPT usage-identity validation while the carrier is present.

#### Scenario: Authenticated reset-credit carrier fails before account routing

- **WHEN** a valid proxy API key sends either reset-credit consume surface with the Daybreak carrier
- **THEN** ingress returns HTTP 400 `required_capability_transport_unsupported`
- **AND** no account, ChatGPT identity, credential, route, fetch, or consume operation is reached

#### Scenario: Local usage initialization authenticates without upstream identity lookup

- **WHEN** a valid proxy API key reads a local usage or reset-credit listing with the Daybreak carrier
- **THEN** the existing local API-key response remains available
- **AND** no ChatGPT usage-identity request or upstream account routing occurs

#### Scenario: Headerless reset-credit behavior remains unchanged

- **WHEN** a reset-credit request omits the required-capability carrier
- **THEN** the existing API-key or ChatGPT identity authentication and redemption behavior remains in effect

### Requirement: SQLite redeem-claim cleanup survives repeated cancellation

After a process acquires the SQLite reset-credit redeem claim, the system MUST
treat heartbeat cancellation and drain followed by holder-fenced claim release
as one owned cleanup operation. Repeated caller cancellation while cleanup is
suspended MUST NOT interrupt that operation. Deferred cancellation MUST surface
only after heartbeat shutdown and release finish. Lease expiry MUST remain the
crash or release-error backstop, not routine live-process cancellation cleanup.

#### Scenario: Repeated cancellation cannot strand a live SQLite claim

- **GIVEN** a SQLite redemption holds a durable claim with a heartbeat
- **WHEN** the body is cancelled and cancellation is delivered again after
  claim release starts
- **THEN** the heartbeat is cancelled and drained
- **AND** holder-fenced release finishes before cancellation surfaces
- **AND** a successor can acquire immediately without waiting for lease expiry

### Requirement: Redemption outcomes provide credit-level evidence

The shared ledger SHALL distinguish pending, retryable, unknown, confirmed reset, explicit no-reset, and expired outcomes. It SHALL retain the original credit id and expiry, request id, origin, attempt timing/count, retry scheduling, upstream result code, reset-window count, and redeemed timestamp when known. A confirmed reset SHALL require a matching redeemed-credit receipt with `code=reset` and positive `windows_reset`, or an equivalent persisted confirmed receipt. Other explicit results SHALL retain their actual meaning, and unrecognized or ambiguous results SHALL remain unknown. Manual and automatic paths SHALL emit credential-free audit records with credit-level outcome evidence. A failed usage refresh SHALL be recorded separately from a confirmed redemption. Existing account routing policies SHALL NOT change solely because a credit disappeared or a consume was attempted.

#### Scenario: Pin followed by a failed consume is recoverable
- **GIVEN** a request is durably pinned before a transient upstream failure
- **WHEN** recovery observes the exact same credit still available before expiry
- **THEN** a bounded retry uses the same credit and request id
- **AND** the pin alone does not count as success

#### Scenario: Lost response and absent credit remain uncertain
- **GIVEN** a consume response was lost and there is no stored confirmed receipt
- **WHEN** a fresh read no longer lists the target credit
- **THEN** the outcome remains unknown unless authoritative evidence resolves it
- **AND** no replacement credit is selected

#### Scenario: HTTP success with no reset is not counted as a reset
- **WHEN** upstream returns HTTP 200 with an explicit no-reset result
- **THEN** the ledger and audit retain that result without a confirmed-reset count
- **AND** the available-credit count is reconciled from evidence rather than blindly decremented

#### Scenario: Quota refresh failure does not repeat consumption
- **GIVEN** a confirmed reset was persisted
- **WHEN** the subsequent usage refresh fails
- **THEN** reconciliation retries the refresh without another consume
- **AND** the confirmed receipt remains available across replicas

#### Scenario: Historical pins are not backfilled as success
- **WHEN** an existing deployment upgrades with legacy pin-only rows
- **THEN** those rows are classified as unknown and keep their original credit mapping

### Requirement: Dashboard can reconcile a single account summary

The dashboard SHALL expose authenticated `GET /api/accounts/{account_id}/summary`, returning the existing account-summary fields including reset-credit freshness using targeted local reads. The endpoint SHALL NOT enumerate all accounts or perform upstream requests. Missing and pending-deletion accounts SHALL return a dashboard 404 response. The canonical path and trailing-slash behavior SHALL retain dashboard authentication and error-envelope rules.

#### Scenario: Targeted account reconciliation
- **WHEN** the dashboard requests a known account's summary after redemption
- **THEN** the response contains that account only, using the same summary mapping as the list
- **AND** no full account enumeration or upstream request occurs

#### Scenario: Deleted account is not restored by reconciliation
- **WHEN** the requested account is missing or pending deletion
- **THEN** the endpoint returns a dashboard 404 instead of an account summary

### Requirement: Simultaneous expiries use bounded parallel redemption

Automatic redemption SHALL reserve four concurrent deadline slots per replica, independently of three discovery slots and one quota-verification slot. A target account SHALL occupy at most one deadline slot on a replica. All consumes SHALL retain the shared per-account serializer and exact-credit checks across replicas. Under a synthetic workload of 100 known eligible credits each with 300 seconds remaining, 10 seconds total for the pre-consume fetch and consume combined per successful redemption, and stalled quota verification, all 100 consumes SHALL finish before expiry on one replica. This capacity test SHALL NOT assume upstream outages or unbounded request latency can be overcome.

#### Scenario: A five-minute burst is not serialized behind quota refresh
- **GIVEN** 100 known eligible accounts each have an exact target credit expiring in 300 seconds
- **AND** each successful redemption takes 10 seconds total for its pre-consume fetch and consume while quota verification stalls
- **WHEN** automatic deadline processing executes
- **THEN** all 100 target credits finish consumption before expiry with no more than four concurrent consumes
- **AND** no account consumes a second credit as part of retry or verification

### Requirement: Reset scheduler survives consume session cleanup
The scheduler SHALL retain usable queued work after the originating database session closes and SHALL continue processing other accounts after a confirmed consume.

#### Scenario: Confirmed consume followed by queued accounts
- **WHEN** a real automatic consume confirms a reset and its session is closed
- **THEN** quota verification and remaining deadline work SHALL continue without accessing expired ORM attributes

### Requirement: Received reset receipts survive transient settlement failure
The system SHALL use bounded cancellation-safe retries to persist a received consume receipt without repeating the upstream consume.

#### Scenario: Transient write failure after confirmation
- **WHEN** the first receipt write fails but a subsequent bounded write succeeds
- **THEN** the confirmed receipt SHALL be durable and usage verification SHALL remain recoverable with exactly one upstream consume

#### Scenario: Cancellation during settlement
- **WHEN** the caller is cancelled after receiving a consume result
- **THEN** owned settlement SHALL finish before cancellation propagates and SHALL leave no detached settlement task

### Requirement: Manual consumes reconcile credit identity
Under the account serializer, manual consumes SHALL consult durable outcomes for the selected credit before creating a new consume request.

#### Scenario: New request ID with stale available credit
- **WHEN** a new manual request selects a credit with a terminal durable outcome
- **THEN** it SHALL NOT send another upstream consume for that credit

#### Scenario: Legacy caller retries without a request body
- **WHEN** a no-body manual consume fails transiently and the same selected credit remains available
- **THEN** a no-body retry SHALL reuse that credit's existing unresolved request ID
- **AND** SHALL NOT create a second upstream consume identity for that credit

#### Scenario: Browser reload creates a new client request ID
- **WHEN** a new manual client ID selects a credit with an unresolved durable attempt
- **THEN** it SHALL be pinned to that credit and reuse the original upstream request identity
- **AND** later retries of either ID SHALL resolve that original receipt rather than consume a different credit

#### Scenario: Alias retention and replica clock skew
- **WHEN** an alias remains within its retention window while the canonical pin is older, or the alias was created on a slower replica clock
- **THEN** the canonical receipt and request identity SHALL remain available for alias replay
- **AND** purge SHALL retain the credit's pins while any pin remains within the retention window
- **AND** alias timestamps SHALL NOT make an alias the upstream request owner

### Requirement: Targeted reset summaries preserve authoritative metadata
Snapshot reconciliation SHALL NOT increase the upstream availability count. Targeted account summaries SHALL preserve the duplicate-identity classification of the full account list.

#### Scenario: Zero count with stale available items
- **WHEN** the post-consume snapshot reports zero available credits alongside stale available items
- **THEN** reconciliation SHALL retain zero available credits

#### Scenario: Targeted summary for duplicate identity
- **WHEN** an account is classified as a duplicate in the full list
- **THEN** its targeted summary SHALL retain that classification without loading the full account list

### Requirement: Automatic retry respects remaining lifetime
Automatic retry scheduling SHALL use the persisted retry time and remaining lifetime of the original credit, and SHALL NOT defer every retry past its expiry when time remains for another bounded attempt.

#### Scenario: Transient failure twenty seconds before expiry
- **WHEN** a fast transient attempt fails with twenty seconds left
- **THEN** a retry SHALL be scheduled before expiry using the same credit and request identity
- **AND** attempts SHALL cease after expiry without a rapid retry loop
