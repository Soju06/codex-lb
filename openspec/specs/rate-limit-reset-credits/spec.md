# rate-limit-reset-credits Specification

## Purpose
Governs visibility and redemption of upstream banked rate-limit reset credits per account. Upstream exposes the redeem affordance only in selected editors, so operators managing many accounts had no way to see how many credits an account holds, when they expire, or to redeem one from the dashboard. This capability defines the per-account polling cadence and in-memory cache, operator redemption of the soonest-expiring credit, isolation of polling failures from account status, and cross-replica serialization of redemption and cache invalidation.
## Requirements
### Requirement: Reset credits are polled per account on a fixed cadence

The system SHALL poll upstream `GET /wham/rate-limit-reset-credits` for each eligible account on a fixed 60-second cadence, using that account's stored OAuth bearer token and `chatgpt-account-id`. The scheduler SHALL start with the application lifespan when reset-credit polling is enabled. Because snapshots are kept in process-local memory, every running replica SHALL refresh its own snapshot cache instead of relying on leader election, and the scheduler SHALL NOT be leader-gated while snapshots remain process-local. Each replica SHALL apply a randomized startup delay of up to one full interval and randomized per-tick jitter of +/-10% so replica ticks are desynchronized. The aggregate upstream fetch rate scales with the number of running replicas (one fetch per eligible account per 60 seconds per replica); the cadence is a fixed application constant and MUST NOT be operator-configurable. The poll SHALL skip any account that is paused, requires reauthentication, deactivated, or lacks a usable `chatgpt-account-id`.

When dashboard setting `auto_redeem_reset_credits_before_expiry` is enabled, the refresh loop SHALL evaluate refreshed snapshots and attempt to redeem the soonest-expiring available reset credit when it expires within five minutes by reusing the existing reset-credit redemption function, serialization, idempotency ledger, cache invalidation, and usage-refresh path. Before invoking the redemption function, automatic redemption SHALL re-read the target account in the redemption session and abort without consuming upstream when the account is missing, paused, requires reauthentication, deactivated, or no longer has a usable `chatgpt-account-id`. Automatic redemption SHALL constrain the redemption helper to the credit id and expiry that triggered the five-minute window, and SHALL abort without consuming upstream if the helper's fresh pre-consume fetch no longer reports that same credit with the same expiry as available. Automatic redemption SHALL use a stable automatic redeem request id for the account and UTC expiry date, and SHALL NOT issue another upstream consume when that automatic request is already durably pinned.

#### Scenario: Default cadence polls every 60 seconds
- **WHEN** the application starts
- **THEN** each eligible account's credits are fetched from upstream at most once per 60 seconds plus the jitter bound
- **AND** a `CODEX_LB_RATE_LIMIT_RESET_CREDITS_REFRESH_INTERVAL_SECONDS` value in the environment is ignored with the removed-setting warning

#### Scenario: Every replica refreshes its local cache
- **WHEN** the application is deployed with multiple running replicas
- **THEN** each replica refreshes its own in-memory reset-credit snapshots on the fixed cadence
- **AND** dashboard reads served by any replica can observe populated reset-credit data after that replica's refresh tick

#### Scenario: Two replicas do not fetch in lockstep
- **GIVEN** two replicas start
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
- **AND** duplicate automatic attempts for an already pinned automatic request do not issue another upstream consume

#### Scenario: Automatic redemption ignores non-expiring snapshots
- **GIVEN** `auto_redeem_reset_credits_before_expiry` is enabled
- **AND** a refreshed eligible account snapshot has no available credit with `expires_at`
- **WHEN** the reset-credit refresh loop processes that account
- **THEN** the system does not attempt automatic redemption for that snapshot

#### Scenario: Automatic redemption waits until the five-minute expiry window
- **GIVEN** `auto_redeem_reset_credits_before_expiry` is enabled
- **AND** a refreshed eligible account snapshot's soonest available credit expires more than five minutes in the future
- **WHEN** the reset-credit refresh loop processes that account
- **THEN** the system refreshes the snapshot but does not attempt automatic redemption

### Requirement: Reset credit snapshots are cached in memory keyed by account

The system SHALL store the most recent successful reset-credits response per account in an in-memory store keyed by account id. The store SHALL be concurrency-safe and SHALL provide an `invalidate(account_id)` operation. Account-summary mappers SHALL join the cached snapshot onto each account summary, exposing `available_reset_credits` (integer) and `reset_credit_nearest_expires_at` (ISO timestamp or null). Accounts with no cached snapshot SHALL expose `available_reset_credits: 0` and `reset_credit_nearest_expires_at: null`.

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

### Requirement: Operators can redeem the soonest-expiring available credit

The system SHALL expose a dashboard endpoint `POST /api/accounts/{account_id}/rate-limit-reset-credits/consume` that redeems exactly one credit for the named account. The endpoint SHALL select, from the freshest cached snapshot, the credit whose `status` is `available` with the smallest `expires_at`, generate a `redeem_request_id` (UUID v4), and forward `{credit_id, redeem_request_id}` to upstream `POST /wham/rate-limit-reset-credits/consume` using the account's bearer token and `chatgpt-account-id`. Before forwarding the consume, the endpoint SHALL durably record the selected `credit_id` against the request's `redeem_request_id` in the shared database; a retry carrying the same `redeem_request_id` MUST reuse that recorded `credit_id` even when served by a different replica. A cached snapshot with `available_count <= 0` MUST be treated as having no redeemable credits, even if the cached `credits` list contains an item marked `available`. When the fresh pre-consume fetch reports `available_count <= 0` or no available credit items, the endpoint SHALL replace any prior cached snapshot for that account with the fresh upstream snapshot before returning a conflict. This SHALL hold even when the caller supplies a `redeem_request_id` for which no durable ledger pin exists: absent a durable pin there is no proof the request is an idempotent retry, so the fresh empty fetch is authoritative and the endpoint MUST NOT pin and consume a stale cached credit. Only a pre-existing durable pin (`(account_id, redeem_request_id) -> credit_id`) authorizes forwarding that pinned credit to upstream when the fresh fetch shows no currently-available credit. On a 200 response the endpoint SHALL invalidate the cached snapshot for that account and return `{code, windows_reset, redeemed_at}`. The endpoint SHALL require dashboard write access; read-only guests MUST be refused.

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
- **THEN** that replica forwards C to upstream instead of selecting a new credit

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

### Requirement: Reset credit redemption is serialized and idempotent across replicas

Per-account redemption serialization MUST hold across all replicas and processes sharing one database. On PostgreSQL the system SHALL use `pg_advisory_xact_lock` keyed by the account id on the caller's session. On SQLite the system SHALL acquire a durable claim row via a single atomic conditional upsert (`INSERT ... ON CONFLICT(account_id) DO UPDATE ... WHERE expires_at < now`) with a 30-second lease, a bounded retry loop that surfaces a client-facing conflict on timeout, release on completion, and takeover of expired claims. While the redeem section runs, the claim holder SHALL renew its lease on a heartbeat cadence shorter than the lease (10 seconds) so a redemption that legitimately outlives one lease (e.g. slow upstream fetch/consume) is NOT taken over by a concurrent process; lease expiry without renewal remains the crash-recovery path. A claim-acquisition timeout SHALL surface in the caller surface's native error envelope: the dashboard error envelope on the dashboard consume endpoint and the `/v1/*` OpenAI error envelope (HTTP 409) on `POST /v1/reset-credit`. The system SHALL persist the `(account_id, redeem_request_id) -> credit_id` mapping in the shared database, committed inside the serialized section BEFORE the upstream consume call; a retry carrying the same `redeem_request_id`, served by ANY replica, MUST resolve to the originally selected `credit_id` and MUST NOT consume a different credit. Ledger rows SHALL be retained at least 24 hours (including after a failed consume, so a retry retargets the same credit) and purged opportunistically afterwards. Expired rows for an account SHALL be purged BEFORE a new pin is inserted, so that reusing a `redeem_request_id` after its prior row has aged past the 24h TTL durably re-pins the new attempt to its newly selected `credit_id` instead of silently discarding the new pin because an `ON CONFLICT DO NOTHING` insert collided with the soon-purged expired row. The pin lookup SHALL apply the same 24h TTL on read: a ledger row whose `created_at` is older than the TTL MUST be treated as absent (not returned as a durable pin) so a reused `redeem_request_id` is re-selected against the fresh fetch and re-pinned rather than forwarded for the stale expired `credit_id`; the read TTL and the purge TTL SHALL be the same duration. Both the dashboard consume endpoint and `POST /v1/reset-credit` SHALL redeem inside this cross-replica serialized section.

#### Scenario: Retry lands on a second replica and reuses the pinned credit
- **GIVEN** replica A redeemed the soonest credit for `redeem_request_id` R but the client never saw the response
- **WHEN** the client retries the consume with the same R and the request is served by replica B
- **THEN** replica B forwards the originally pinned `credit_id` to upstream
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
- **GIVEN** an account has a ledger row for `redeem_request_id` R pinned to credit C1 whose `created_at` is older than the 24h TTL
- **WHEN** a new redemption reuses R and selects a different credit C2
- **THEN** the expired row is purged before the new insert so the ledger persists `(R -> C2)`
- **AND** a same-R retry served by any replica retargets C2, not the discarded C1

#### Scenario: Expired pin is ignored on read
- **GIVEN** an account has a ledger row for `redeem_request_id` R whose `created_at` is older than the 24h TTL
- **WHEN** the pin lookup for `(account_id, R)` runs before any purge write
- **THEN** the lookup returns no durable pin (the expired row reads as absent)
- **AND** the redemption re-selects against the fresh fetch and re-pins the newly selected credit rather than forwarding the stale expired `credit_id`

#### Scenario: Claim contention on the v1 surface uses the OpenAI envelope
- **GIVEN** another process holds the redeem claim for the whole acquisition timeout
- **WHEN** a client calls `POST /v1/reset-credit` for that account
- **THEN** the endpoint returns 409 in the `/v1/*` OpenAI error envelope, not the dashboard envelope

### Requirement: Reset credit snapshot invalidation propagates across replicas

After a successful consume (dashboard or `POST /v1/reset-credit`) and after a consume-conflict snapshot invalidation, the system SHALL bump a `reset_credits` namespace on the shared cache-invalidation version counter (best-effort); every PEER replica's invalidation poller SHALL clear its in-memory reset-credits store within the poll bound, with the per-replica refresh tick as the fallback when a bump is lost. The ORIGINATING replica SHALL NOT re-clear its whole reset-credits store in response to its own bump: it has already evicted the affected account's snapshot precisely, so a whole-store clear on the source would needlessly discard still-valid snapshots for unrelated accounts and force redundant upstream refetches. The source replica MAY acknowledge its own bump locally to suppress the self-triggered whole-store clear; if a peer bump coalesces into the same acknowledged version and is thereby not observed on the source, that degrades to the per-replica refresh fallback (identical to a lost bump) and never suppresses invalidation on any peer.

#### Scenario: Peer replica stops listing a redeemed credit within the poll bound
- **GIVEN** replicas A and B both cache a snapshot listing credit C as available
- **WHEN** a consume for credit C succeeds on replica A
- **THEN** replica B's cached snapshot for that account is cleared within the invalidation poll bound
- **AND** replica B no longer lists credit C as available from that stale snapshot

#### Scenario: Lost bump converges at the next refresh tick
- **GIVEN** the version-counter bump write fails after a successful consume
- **WHEN** replica B's next scheduled refresh tick runs
- **THEN** replica B's snapshot for that account reflects the post-redeem upstream state no later than that tick

#### Scenario: Redeeming one account does not clear unrelated snapshots on the source replica
- **GIVEN** replica A caches valid snapshots for account X and account Y
- **WHEN** a consume for account X succeeds on replica A and bumps the `reset_credits` namespace
- **THEN** replica A evicts only account X's snapshot
- **AND** account Y's cached snapshot on replica A survives (replica A does not clear its whole store in response to its own bump)

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

### Requirement: Reset credit polling can be disabled

The dashboard setting `rate_limit_reset_credits_refresh_enabled` (a nullable `dashboard_settings` column; NULL inherits the deprecated `CODEX_LB_RATE_LIMIT_RESET_CREDITS_REFRESH_ENABLED` environment variable, then the default `true`) SHALL enable or disable background reset-credit polling. It SHALL be exposed with provenance on `GET`/`PUT /api/settings` (Settings → Advanced → Background jobs). The polling loop SHALL always start; each refresh cycle SHALL read the effective value from the dashboard-settings snapshot before taking its lock and SHALL skip the cycle (no upstream fetch, no automatic redemption) while it is `false`, so a change applies on the next cycle on every replica without a restart. Because the refresh loop is the sole driver of automatic reset-credit redemption, disabling polling SHALL also disable automatic redemption; when polling is effectively disabled while the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is enabled, the system SHALL log a configuration-conflict warning at startup naming both settings. The dashboard settings update SHALL reject, with a bad-request error naming the polling toggle, any request that would newly produce the unrunnable pair "auto-redeem enabled, polling effectively disabled" — both the request that newly enables `auto_redeem_reset_credits_before_expiry` and the request that disables the polling toggle while the opt-in stays enabled. The gate SHALL evaluate the proposed effective values (a value in the request, else the inherited value when the request clears it, else the current effective value). Setting both consistently in one request (both on, or both off) SHALL succeed, and a payload that only re-saves an already inconsistent pair SHALL remain accepted so unrelated settings edits are not blocked.

#### Scenario: Operator disables background polling

- **GIVEN** the polling loop was started with `rate_limit_reset_credits_refresh_enabled` effectively `true`
- **WHEN** an operator sets `rate_limit_reset_credits_refresh_enabled` to `false` in the dashboard
- **THEN** the next refresh cycle performs no upstream reset-credits fetch and no automatic redemption
- **AND** setting it back to `true` (or clearing it so the inherited `true` applies) makes the following cycle fetch again
- **AND** no replica was restarted

#### Scenario: Disabled polling conflicts with persisted auto-redeem opt-in

- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is effectively `false` (dashboard value, or environment alias while the dashboard value is unset)
- **AND** the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is `true`
- **WHEN** the application starts
- **THEN** the system logs a configuration-conflict warning naming both settings
- **AND** no automatic reset-credit redemption occurs while polling remains disabled

#### Scenario: Auto-redeem opt-in is rejected while polling is disabled

- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is effectively `false`
- **AND** the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is `false`
- **WHEN** a dashboard settings update sets `auto_redeem_reset_credits_before_expiry` to `true` without also enabling the polling toggle
- **THEN** the update is rejected with a bad-request error naming the polling toggle
- **AND** the persisted setting remains `false`

#### Scenario: Disabling polling is rejected while auto-redeem is enabled

- **GIVEN** the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is `true`
- **AND** `rate_limit_reset_credits_refresh_enabled` is effectively `true`
- **WHEN** a dashboard settings update sets `rate_limit_reset_credits_refresh_enabled` to `false` without also turning the opt-in off
- **THEN** the update is rejected with the same bad-request error naming the polling toggle
- **AND** the polling toggle remains effectively `true`
- **AND** turning both off in one request succeeds

#### Scenario: Enabling polling and auto-redeem in one request succeeds

- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is `false` in the dashboard
- **WHEN** a dashboard settings update sets both `auto_redeem_reset_credits_before_expiry` and `rate_limit_reset_credits_refresh_enabled` to `true`
- **THEN** the update succeeds and both values are persisted

#### Scenario: Persisted auto-redeem does not block unrelated settings edits

- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is effectively `false`
- **AND** the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is already `true`
- **WHEN** a full settings payload that keeps the opt-in unchanged is submitted
- **THEN** the update succeeds

#### Scenario: Environment alias applies only while the dashboard value is unset

- **GIVEN** `CODEX_LB_RATE_LIMIT_RESET_CREDITS_REFRESH_ENABLED=false` and no dashboard value
- **WHEN** an operator sets `rate_limit_reset_credits_refresh_enabled` to `true` in the dashboard
- **THEN** refresh cycles fetch again and `provenance.rate_limit_reset_credits_refresh_enabled.source` is `dashboard`

