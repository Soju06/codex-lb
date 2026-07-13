# rate-limit-reset-credits — replica-safe-reset-credits deltas

## ADDED Requirements

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

## MODIFIED Requirements

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

### Requirement: Reset credits are polled per account on a fixed cadence

The system SHALL poll upstream `GET /wham/rate-limit-reset-credits` for each eligible account on a configurable cadence that defaults to 60 seconds, using that account's stored OAuth bearer token and `chatgpt-account-id`. The scheduler SHALL always start with the application lifespan. Because snapshots are kept in process-local memory, every running replica SHALL refresh its own snapshot cache instead of relying on leader election, and the scheduler SHALL NOT be leader-gated while snapshots remain process-local. Each replica SHALL apply a randomized startup delay of up to one full interval and randomized per-tick jitter of +/-10% so replica ticks are desynchronized. The aggregate upstream fetch rate scales with the number of running replicas; `rate_limit_reset_credits_refresh_interval_seconds` is the operator control for total upstream load. The poll SHALL skip any account that is paused, requires reauthentication, deactivated, or lacks a usable `chatgpt-account-id`.

#### Scenario: Default cadence polls every 60 seconds
- **WHEN** the application starts with default settings
- **THEN** each eligible account's credits are fetched from upstream at most once per 60 seconds plus the jitter bound

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
