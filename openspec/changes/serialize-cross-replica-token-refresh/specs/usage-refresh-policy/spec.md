# usage-refresh-policy (delta)

## ADDED Requirements

### Requirement: Cross-replica token refresh serialization

Before any upstream OAuth token exchange for an account, the system MUST acquire that account's row in `account_refresh_claims` via a conditional upsert that succeeds only when no unexpired claim by another claimant exists; the upsert MUST be atomic on both PostgreSQL (ON CONFLICT row lock) and SQLite (single-writer lock). After acquiring, the system MUST re-read the account's refresh-token material fresh from the database (bypassing session identity caches) and MUST skip the upstream exchange when the material has rotated since the refresh was requested, adopting the stored tokens instead. Claims MUST carry an expiry covering all work performed under the claim (TTL at least the refresh-admission wait timeout plus twice the refresh HTTP timeout, because the claim is held across the admission wait and the OAuth exchange) so a crashed claimant cannot block refresh indefinitely while a healthy claimant cannot lose its claim mid-work, MUST be released after the refreshed tokens are persisted, and MUST NOT be held as an open database transaction or lock across upstream network I/O. When the claim TTL is not explicitly configured, the system MUST derive its default to at least this floor from the related timeout settings, so a deployment that predates the claim-TTL setting but raised the refresh or admission timeouts still starts up (never crashing during settings construction against a fixed default); the system MUST reject only an explicitly configured TTL below the floor. The claimant identity MUST remain unique per OS process even when the configured instance id exceeds the stored column width (truncate the instance-id portion, never the per-process suffix). The per-process suffix MUST be derived per OS process and resolved at claim-build time (for example incorporating `os.getpid()`), never frozen at module import: in pre-fork/multi-worker deployments a module imported before the fork boundary MUST NOT hand every forked child an identical suffix, so two sibling workers sharing one instance id build DISTINCT claimant identities (and thus distinct `claimed_by` values) rather than both satisfying the re-entrant claim upsert and refreshing the single-use token concurrently. The suffix MUST also remain stable across repeated calls within a single process so genuine same-process re-entrant claims still match.

Claim ownership MUST be per-refresh, not process-wide: the stored claim identity MUST combine the claimant (replica/process) identity with a per-refresh owner token derived from the refresh-token material being exchanged (its fingerprint). The re-entrant same-owner takeover that lets a crashed refresh reclaim its own live claim MUST match only when BOTH the claimant AND the owner token are identical; a release MUST delete only the exact composed claim. Consequently, when two refreshes for the same account run in one process with different token fingerprints (for example a re-auth/import lands while an older forced refresh is still in flight), the second refresh MUST contend for the claim (wait until the first releases or the claim expires) rather than re-entering the first refresh's live claim, and neither refresh's release MAY delete the other's claim. The composed claim identity MUST fit the stored column width without truncating either the per-process suffix or the owner token.

After a successful upstream exchange, the system MUST persist the newly issued tokens with a compare-and-set conditioned on the refresh-token ciphertext the exchange was requested with. When that compare-and-set misses, the system MUST NOT assume any ciphertext change is a newer rotation: it MUST compare the freshly observed refresh-token material against the material this attempt exchanged (by decrypted-plaintext fingerprint, because token ciphertext is non-deterministic and a concurrent re-authentication or import can re-encrypt the same plaintext to different bytes). Only when the stored material is a genuinely different refresh token MUST the system adopt the stored row without persisting its own result; when the stored material is the same plaintext merely re-encrypted, the system MUST retry the compare-and-set against the freshly observed ciphertext (bounded) so its own single-use rotation is persisted rather than discarding it and leaving the account holding the token it already consumed upstream. When the bounded retries are exhausted without the compare-and-set ever landing, the newly issued single-use token was already produced by the successful upstream exchange (which consumed the previously stored single-use token), so the system MUST NOT drop that rotation: after a successful exchange, the stored row MUST end holding the newly issued (usable) refresh token, never the already-consumed one. On exhaustion the system MUST first re-read the stored material once more and adopt it when a genuinely different peer rotation has landed; otherwise it MUST escalate to an unconditional write (keyed on account id only, not gated on the exchanged ciphertext) that forces the freshly issued material into the row, and then report success so the caller mirrors the rotated tokens. The system MUST raise a transient (non-permanent) refresh error that is not recorded in the permanent-failure cooldown ONLY when even that forced write cannot land because the row no longer exists — in which case there is no consumed token left stored to reuse. The system MUST NOT raise while leaving the already-consumed token as the authoritative stored value, because that would turn transient re-encryption contention into a permanent refresh-token-reuse failure on the next request.

#### Scenario: Two replicas force-refresh the same account concurrently

- **GIVEN** two replicas hold the same refresh-token material for one account
- **WHEN** both trigger a forced token refresh concurrently (for example after a shared upstream 401)
- **THEN** exactly one upstream token exchange occurs
- **AND** the account remains `active`
- **AND** both replicas end up with the rotated token material
- **AND** the account's sticky sessions and bridge sessions are untouched

#### Scenario: Claimant crashes mid-refresh

- **GIVEN** a replica acquired the refresh claim for an account and crashed before releasing it
- **WHEN** another replica attempts to refresh the account after the claim TTL has elapsed
- **THEN** the claim acquisition succeeds and the refresh proceeds

#### Scenario: Timeout-only config predating the claim TTL setting still boots

- **GIVEN** a deployment that raised the refresh HTTP timeout or the admission wait timeout above the values that keep the fixed 30s default above the floor
- **AND** that deployment does not explicitly configure the claim TTL
- **WHEN** settings are constructed
- **THEN** construction succeeds with a claim-TTL default derived to at least the floor (admission wait plus twice the refresh timeout)
- **AND** an explicitly configured claim TTL below the floor is still rejected

#### Scenario: Two refreshes in one process with different fingerprints contend

- **GIVEN** a refresh for an account is in flight in a process, holding the account's claim under one refresh-token fingerprint
- **WHEN** a second refresh for the same account starts in the same process with a different refresh-token fingerprint (for example after a re-auth/import)
- **THEN** the second refresh does NOT re-enter the live claim and instead contends (waits until the first releases or the claim expires)
- **AND** releasing either refresh's claim does not delete the other refresh's claim

#### Scenario: Winner adopts a rotation that landed before its claim

- **GIVEN** a replica acquires the refresh claim for an account
- **AND** the freshly re-read refresh-token material differs from the material the refresh was requested with
- **WHEN** the replica proceeds
- **THEN** it returns the stored tokens without any upstream token exchange

#### Scenario: Persistence compare-and-set misses on a re-encryption of the same token

- **GIVEN** a replica completed a successful upstream token exchange and holds the newly issued single-use tokens
- **AND** a concurrent re-authentication/import re-encrypted the SAME refresh-token plaintext to different ciphertext, so the persistence compare-and-set misses
- **WHEN** the replica re-reads the stored material and finds its refresh-token fingerprint unchanged from the material it exchanged
- **THEN** it retries the compare-and-set against the freshly observed ciphertext and persists its own newly issued tokens
- **AND** it does not adopt the re-encrypted, already-consumed token

#### Scenario: Persistence compare-and-set never lands within the bounded retries

- **GIVEN** a replica completed a successful upstream token exchange and holds the newly issued single-use tokens
- **AND** the persistence compare-and-set keeps missing on same-plaintext re-encryption until the bounded retry budget is exhausted
- **AND** a final re-read shows no genuinely different peer rotation
- **WHEN** the replica has not persisted its rotated tokens after the final compare-and-set attempt
- **THEN** it escalates to an unconditional write (keyed on account id only) that forces the freshly issued tokens into the stored row
- **AND** the stored row ends holding the newly issued (usable) refresh token, never the already-consumed one
- **AND** it reports success without raising, so the account does not fail permanently on the next refresh

#### Scenario: Forced persistence write cannot land because the row is gone

- **GIVEN** a replica completed a successful upstream token exchange and holds the newly issued single-use tokens
- **AND** the persistence compare-and-set keeps missing until the bounded retry budget is exhausted
- **AND** the account row has since been deleted, so even the unconditional forced write matches no row
- **WHEN** the forced write fails to persist
- **THEN** it raises a transient, non-permanent refresh error that is not recorded in the permanent-failure cooldown
- **AND** no already-consumed token is left as the authoritative stored value (the row no longer exists)

#### Scenario: Persistence compare-and-set misses on a genuine peer rotation

- **GIVEN** a replica completed a successful upstream token exchange
- **AND** a peer committed a genuinely different refresh token, so the persistence compare-and-set misses
- **WHEN** the replica re-reads the stored material and finds its refresh-token fingerprint changed
- **THEN** it adopts the peer's stored tokens without persisting its own result

### Requirement: Refresh claim losers wait bounded and never degrade account status

A process that fails to acquire the refresh claim MUST wait by polling within a bounded deadline (configurable cap, additionally bounded by the caller's refresh timeout budget). When it observes rotated refresh-token material it MUST return the stored tokens without an upstream call. When the deadline elapses it MUST fail with a transient (non-permanent) refresh error that is not recorded in the permanent-failure cooldown, and it MUST NOT write `reauth_required` or `deactivated`, so token-refresh recovery fails over to another account instead of blocking.

When a proxy stream turn NOT hard-pinned to a required account encounters this transient claim failure, the streaming retry loop MUST exclude the affected account and fail over to a different account rather than reselecting the claimed account until attempts are exhausted. This failover MUST apply to both the proactive freshness check on the first stream attempt (before any upstream 401) and the forced refresh on the post-401 recovery attempt. Before failing over, the loop MUST release the stream lease it already acquired for the skipped account so that account does not continue to consume one of its stream-concurrency slots for a stream that will never open. On this transient-claim failover the loop MUST also record a retryable `upstream_unavailable` stream error (mirroring the transient aiohttp/connect failover and the WebSocket connect loop): when EVERY candidate account hits a transient refresh-claim timeout before the stream opens and attempts are exhausted, the client MUST receive the temporary `upstream_unavailable` (retryable/capacity) condition rather than a misleading generic `no_accounts` response.

When a proxy stream turn IS hard-pinned to a required account — a session-continuity `previous_response_id` bound to a preferred account or a file-required preferred account, which sets `preferred_account_id` (and, for `previous_response_id`, `require_preferred_account`) — the movable failover above is correctly skipped so the request never crosses accounts (preserving the account-ownership invariant). But the streaming retry loop MUST NOT then fall through to an unconditional reselect that reselects the same pinned account until attempts are exhausted: on a transient (transport-level / non-permanent) refresh-claim failure for a hard-pinned stream, the loop MUST release the pinned account's already-acquired stream lease (no leaked slot) and MUST surface a retryable `upstream_unavailable` error promptly rather than spinning pointlessly on the held claim and then surfacing a misleading `no_accounts` result. This hard-pinned handling MUST apply symmetrically to BOTH the proactive freshness check on the first stream attempt (before any upstream 401) AND the forced (`force=True`) refresh on the post-401 recovery attempt, so a hard-pinned stream that opens, receives a 401, and then hits a transient claim timeout on its forced refresh also stays on the owner, releases the lease, and surfaces the retryable `upstream_unavailable` promptly instead of reselecting the same owner until exhaustion. The transient claim contention MUST NOT be recorded as a permanent failure. This does not apply to a locally verified cross-transport fresh-replay body, which may still move off the failed owner as specified elsewhere.

The WebSocket connect loop MUST apply the same failover for a transient, transport-level claim failure reaching the connect path (on both the proactive freshness check and the post-401 forced refresh): rather than surfacing a bogus 401 `invalid_api_key`, it MUST release the skipped account's already-acquired stream lease, exclude the account, and reselect a healthy account. This failover MUST be gated only on whether the request is *hard-pinned to a required account* — that is, session-continuity (a `previous_response_id` bound to a preferred account) or a file-required preferred account; it MUST NOT be suppressed merely because a *soft* preferred account is set. In particular, a forced-refresh reconnect auth replay sets the stale account as both the forced-refresh target and the preferred account, but a movable request (no session continuity, no file pin) MUST still exclude the stale account and fail over on a transient transport claim failure. Only a hard-pinned request MUST stay on its required account and surface the error there, preserving the account-ownership invariant for session-continuity and file-pinned requests. When every account attempt is exhausted by such transient claim failovers, the connect loop MUST emit a proper terminal error to the client (a 503/capacity-style upstream error, not a 401 `invalid_api_key` and not a silent no-op that leaves the client waiting).

The compact-responses path MUST apply the same failover for a transient, transport-level claim failure raised on BOTH its proactive `_ensure_fresh_with_budget` freshness-check preflight AND the post-401 forced (`force=True`) refresh recovery attempt: rather than letting the non-permanent `RefreshError` escape unhandled on the preflight (which surfaces to the client as an unhandled server error) or re-raising the original upstream 401 on the post-401 recovery (which surfaces a misleading `invalid_api_key`), it MUST record the failure as a transient `upstream_unavailable` error, exclude the account, and reselect a healthy account within the compact account-attempt loop. The preflight branch MUST additionally release the selected account's `response_create` lease before failover. When the request is pinned to a preferred account, both branches MUST instead surface a retriable upstream-unavailable error on that account rather than crossing to another account. When EVERY candidate account hits the transient claim timeout and the account-attempt loop is exhausted, the client MUST receive the recorded retryable `upstream_unavailable` error rather than the misleading original 401. A permanent or non-transport refresh failure MUST keep its prior escalation (it propagates to the caller) rather than being reinterpreted as a transient failover.

#### Scenario: Claim held by another replica past the wait cap

- **GIVEN** an unexpired refresh claim held by another replica
- **WHEN** a refresh waits past the configured wait cap without observing rotated token material
- **THEN** the refresh fails with a transient, non-permanent error
- **AND** the account status is unchanged
- **AND** sticky and bridge sessions are untouched
- **AND** the failure is not cached as a permanent refresh failure

#### Scenario: Winner finishes within the wait cap

- **GIVEN** an unexpired refresh claim held by another replica that completes its token exchange
- **WHEN** the waiting replica observes the rotated refresh-token material within the wait cap
- **THEN** it returns the rotated tokens with zero upstream token exchanges

#### Scenario: Proactive pre-stream claim timeout fails over instead of looping

- **GIVEN** a proxy stream turn whose first-selected account is stale and needs a proactive refresh
- **AND** that account's refresh claim is held by another replica past the wait cap
- **WHEN** the first-attempt freshness check raises the transient claim error before any upstream 401
- **THEN** the streaming retry loop excludes that account and fails over to a healthy account
- **AND** the excluded account's already-acquired stream lease is released before failover
- **AND** the request does not exhaust attempts as `no_accounts` while a healthy alternate exists

#### Scenario: Stream retry exhausts every account on transient claim failovers

- **GIVEN** a proxy stream turn not pinned to a preferred/required account
- **AND** every candidate account's refresh claim is held by another replica so its proactive freshness check raises the transient claim error before the stream opens
- **WHEN** the streaming retry loop excludes each account and exhausts its attempts
- **THEN** the client receives a retryable `upstream_unavailable` error rather than a generic `no_accounts` response
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Stream retry exhausts every account on post-401 forced-refresh claim failovers

- **GIVEN** a proxy stream turn not pinned to a preferred/required account
- **AND** every candidate account opens far enough to receive an upstream 401, and its subsequent forced (`force=True`) refresh raises the transient claim error because the claim is held by another replica
- **WHEN** the streaming retry loop releases each account's stream lease, excludes it, and exhausts its attempts
- **THEN** the client receives a retryable `upstream_unavailable` error rather than a generic `no_accounts` response
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Hard-pinned stream turn stays on its owner account on transient claim timeout

- **GIVEN** a hard-pinned proxy stream turn (a session-continuity `previous_response_id` bound to a preferred account, which sets `preferred_account_id` and `require_preferred_account`)
- **AND** the pinned owner account's refresh claim is held by another replica so its proactive freshness check raises the transient, transport-level claim error before the stream opens
- **WHEN** the streaming retry loop evaluates the transient claim failure for the pinned request
- **THEN** the loop does NOT cross to another account (the account-ownership invariant is preserved)
- **AND** the pinned account's already-acquired stream lease is released (not leaked)
- **AND** the client receives a retryable `upstream_unavailable` error promptly rather than pointless retries that exhaust into a misleading `no_accounts` response
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Hard-pinned stream turn stays on its owner account on post-401 forced-refresh claim timeout

- **GIVEN** a hard-pinned proxy stream turn (a session-continuity `previous_response_id` bound to a preferred account, which sets `preferred_account_id` and `require_preferred_account`)
- **AND** the pinned owner account's proactive freshness check succeeds so the stream opens, but the upstream returns a 401 and the subsequent forced (`force=True`) refresh raises the transient, transport-level claim error because the claim is held by another replica
- **WHEN** the streaming retry loop evaluates the transient claim failure for the pinned request on the post-401 recovery attempt
- **THEN** the loop does NOT cross to another account (the account-ownership invariant is preserved)
- **AND** the pinned account's already-acquired stream lease is released (not leaked)
- **AND** the client receives a retryable `upstream_unavailable` error promptly rather than reselecting the same owner until attempts exhaust into a misleading `no_accounts` response
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: WebSocket connect claim timeout fails over instead of 401

- **GIVEN** a WebSocket responses connection whose first-selected account needs a refresh
- **AND** that account's refresh claim is held by another replica past the wait cap
- **WHEN** the connect path raises the transient, transport-level claim error
- **THEN** the connect loop excludes that account and fails over to a healthy account
- **AND** the excluded account's already-acquired stream lease is released before failover
- **AND** the client receives the upstream response rather than a 401 `invalid_api_key`

#### Scenario: Movable forced-refresh reconnect fails over on transient claim timeout

- **GIVEN** a movable WebSocket responses request (no session-continuity `previous_response_id`, no file-required preferred account)
- **AND** a reconnect auth replay has set the stale account as both the forced-refresh target and the (soft) preferred account
- **WHEN** the forced refresh on that account raises the transient, transport-level claim error
- **THEN** the connect loop excludes the stale account and fails over to a healthy account
- **AND** the stale account's already-acquired stream lease is released before failover
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Hard-pinned reconnect stays on its required account on transient claim timeout

- **GIVEN** a hard-pinned WebSocket responses request (session-continuity `previous_response_id` bound to a preferred account, or a file-required preferred account)
- **AND** a reconnect auth replay has set that required account as the forced-refresh target
- **WHEN** the forced refresh on that account raises the transient, transport-level claim error
- **THEN** the connect loop does NOT cross to another account
- **AND** the error is surfaced on the pinned account rather than silently failing over
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: WebSocket connect exhausts every account on transient claim failovers

- **GIVEN** a WebSocket responses connection not pinned to a preferred/required account
- **AND** every account attempt (up to the WebSocket max-account-attempts) raises the transient, transport-level claim error
- **WHEN** the connect loop excludes each account and exhausts its attempts
- **THEN** the client receives a proper terminal error frame (a 503/capacity-style upstream error), not a 401 `invalid_api_key` and not a silent no-op
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Compact freshness-check claim timeout fails over instead of erroring out

- **GIVEN** a compact-responses request whose first-selected account is stale and needs a proactive refresh
- **AND** that account's refresh claim is held by another replica past the wait cap
- **WHEN** the freshness-check preflight raises the transient, transport-level claim error
- **THEN** the compact account-attempt loop releases the account's `response_create` lease, excludes that account, and fails over to a healthy account
- **AND** the client receives a normal compact response rather than an unhandled server error
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Compact post-401 forced-refresh claim timeout fails over instead of surfacing 401

- **GIVEN** a compact-responses request not pinned to a preferred account whose selected account returns an upstream 401
- **AND** the post-401 forced (`force=True`) refresh raises the transient, transport-level claim error because the claim is held by another replica
- **WHEN** the compact account-attempt loop records a transient `upstream_unavailable`, excludes that account, and fails over to a healthy account
- **THEN** the client receives a normal compact response rather than the misleading original 401
- **AND** when every candidate account hits the transient claim timeout and attempts are exhausted, the client receives the retryable `upstream_unavailable` error rather than the 401
- **AND** the transient claim contention is never recorded as a permanent failure

## MODIFIED Requirements

### Requirement: token_expired at the refresh boundary deactivates the account

The system MUST treat OAuth refresh credential-token or session errors as
permanent refresh-token/session failures. Codes include `token_expired`,
`app_session_terminated`, `invalid_grant`, `refresh_token_expired`,
`refresh_token_reused`, and `refresh_token_invalidated`. The affected account
MUST be marked `reauth_required` and removed from the routing pool until it is
re-authenticated.

Before persisting a permanent refresh failure, the system MUST re-read the
account's token material from the database with a real SELECT that bypasses
session identity caches, MUST NOT downgrade the account when the refresh token
rotated after the failed attempt began (returning the rotated tokens instead),
and MUST apply the status downgrade with a compare-and-set conditioned on the
freshly observed account state including the refresh-token ciphertext, so a
concurrent re-authentication or rotation — even one that leaves
status/reason/reset untouched — is never overwritten.

When that status compare-and-set misses, a ciphertext change MUST NOT by itself
be treated as a rotation to defer to: because token ciphertext is
non-deterministic, a concurrent re-authentication or import can re-encrypt the
SAME refresh-token plaintext to different bytes between the fresh re-read and
the write. The system MUST compare the freshly observed refresh-token material
against the material this attempt exchanged by decrypted-plaintext fingerprint.
When the fingerprint is genuinely different the system MUST adopt the stored row
without downgrading, and MUST return those rotated tokens to the caller (rather
than returning the success/no-op sentinel that lets the caller re-raise the
original permanent error) — whether the genuine difference is observed at the
initial fresh re-read or only after a status compare-and-set miss. Re-raising in
the compare-and-set-miss window would send proxy callers into the permanent-failure
path (for example `LoadBalancer.mark_permanent_failure()`), whose status write is
NOT guarded by this refresh-token compare-and-set, so it would clobber the peer's
valid rotation with `reauth_required` and tear down sessions for an account a peer
just repaired. When the fingerprint is unchanged — the account is still
holding the very material that just failed permanently — the system MUST re-read
and retry the compare-and-set against the freshly observed ciphertext (bounded)
so the downgrade lands, rather than skipping the status write and leaving the
account active with dead credentials.

#### Scenario: Refresh-time `app_session_terminated` is classified as permanent

- **WHEN** `classify_refresh_error("app_session_terminated")` is evaluated
- **THEN** it returns `True`

#### Scenario: Refresh-time `app_session_terminated` requires re-authentication

- **WHEN** `AuthManager.refresh_account` receives a
  `RefreshError("app_session_terminated", ..., is_permanent=True)` from
  `refresh_access_token`
- **THEN** the account is transitioned to `REAUTH_REQUIRED`
- **AND** the reason references the re-login requirement so the dashboard can
  surface it
- **AND** the account is no longer selected by the load balancer until it is
  re-authenticated

#### Scenario: Concurrent rotation loser receives refresh_token_reused

- **GIVEN** another replica rotated the account's refresh token and committed
  while this replica's exchange with the old token was in flight
- **WHEN** this replica's exchange fails with `refresh_token_reused`
- **THEN** no `reauth_required` write occurs
- **AND** this replica returns the rotated tokens from the database

#### Scenario: Status CAS misses on a re-encryption of the same failing token

- **GIVEN** this replica's exchange failed permanently and the account still
  holds the same refresh-token plaintext that failed
- **AND** a concurrent re-authentication/import re-encrypted that SAME plaintext
  to different ciphertext between the fresh re-read and the status CAS, so the
  CAS misses while status/reason/reset are unchanged
- **WHEN** the guard re-reads and finds the refresh-token fingerprint unchanged
- **THEN** it retries the status CAS against the freshly observed ciphertext and
  lands the `reauth_required` downgrade
- **AND** it does not leave the account active with the dead credentials

#### Scenario: Peer rotation lands in the status-CAS-miss window

- **GIVEN** this replica's exchange failed permanently and the fresh re-read
  still showed the same failing refresh-token material
- **AND** a concurrent re-authentication/rotation committed a genuinely
  different refresh token between that fresh re-read and the status CAS, so the
  CAS misses
- **WHEN** the guard re-reads and finds the refresh-token fingerprint now
  genuinely different from the material this attempt exchanged
- **THEN** it adopts the stored row and returns the peer's rotated tokens to the
  caller
- **AND** no `reauth_required` write occurs and the original permanent error is
  not re-raised, so the caller does not enter the permanent-failure path for the
  already-repaired account

### Requirement: Multi-replica leader guard

Auth Guardian SHALL use the existing leader-election mechanism so only the elected replica performs proactive refresh work. When leader election is disabled, the guardian MUST detect multi-replica operation dynamically from live bridge ring membership (members with a heartbeat within the staleness threshold) in addition to the static instance ring, MUST skip the refresh pass when more than one live replica is detected, and MUST log a warning identifying the leader-election setting.

#### Scenario: Replica is not leader

- **GIVEN** leader election is enabled
- **AND** the current replica does not acquire leadership
- **WHEN** Auth Guardian wakes
- **THEN** the scheduler skips refresh work for that pass

#### Scenario: Dynamically registered replicas without leader election

- **GIVEN** two replicas registered in `bridge_ring_members` with live heartbeats
- **AND** the static instance ring is empty
- **AND** leader election is disabled
- **WHEN** an Auth Guardian tick runs on either replica
- **THEN** the guardian performs no refresh work
- **AND** logs a warning identifying the leader-election setting
