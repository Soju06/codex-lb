# usage-refresh-policy (delta)

## ADDED Requirements

### Requirement: Cross-replica token refresh serialization

Before any upstream OAuth token exchange for an account, the system MUST acquire that account's row in `account_refresh_claims` via a conditional upsert that succeeds only when no unexpired claim by another claimant exists; the upsert MUST be atomic on both PostgreSQL (ON CONFLICT row lock) and SQLite (single-writer lock). After acquiring, the system MUST re-read the account's refresh-token material fresh from the database (bypassing session identity caches) and MUST skip the upstream exchange when the material has rotated since the refresh was requested, adopting the stored tokens instead. Claims MUST carry an expiry covering all work performed under the claim (TTL at least the refresh-admission wait timeout plus twice the refresh HTTP timeout, because the claim is held across the admission wait and the OAuth exchange) so a crashed claimant cannot block refresh indefinitely while a healthy claimant cannot lose its claim mid-work, MUST be released after the refreshed tokens are persisted, and MUST NOT be held as an open database transaction or lock across upstream network I/O. The claim expiry — BOTH the stored `claim_expires_at` AND the takeover predicate that treats an existing claim as expired — MUST be evaluated on the DATABASE server clock (`clock_timestamp()`/`now()` on PostgreSQL; in-statement `strftime(..., 'now')` on SQLite), never against a replica-local Python wall-clock instant captured before the statement executes, so inter-replica clock skew can never let one replica treat another replica's still-live claim as expired and steal it (which would let two replicas exchange the same single-use refresh token concurrently). This mirrors the clock-domain guarantee of the scheduler leader election. When the claim TTL is not explicitly configured, the system MUST derive its default to at least this floor from the related timeout settings, so a deployment that predates the claim-TTL setting but raised the refresh or admission timeouts still starts up (never crashing during settings construction against a fixed default); the system MUST reject only an explicitly configured TTL below the floor. The claimant identity MUST remain unique per OS process even when the configured instance id exceeds the stored column width (truncate the instance-id portion, never the per-process suffix). The per-process suffix MUST be derived per OS process and resolved at claim-build time (for example incorporating `os.getpid()`), never frozen at module import: in pre-fork/multi-worker deployments a module imported before the fork boundary MUST NOT hand every forked child an identical suffix, so two sibling workers sharing one instance id build DISTINCT claimant identities (and thus distinct `claimed_by` values) rather than both satisfying the re-entrant claim upsert and refreshing the single-use token concurrently. The suffix MUST also remain stable across repeated calls within a single process so genuine same-process re-entrant claims still match. The same fork-safety MUST hold for the coordinator that composes claims: a process-default/auto-derived claimant identity MUST NOT be frozen when the coordinator is constructed (the process-default coordinator is commonly built during preload/startup, before a pre-fork server forks its workers, and a frozen identity would be inherited identically by every child). It MUST instead be resolved per OS process at use time so two forked children build DISTINCT claimant identities; a claimant identity that a caller explicitly injects MUST remain stable and unchanged (including across a fork), and repeated reads within one process MUST stay stable.

After acquiring the claim and re-reading the account fresh, and BEFORE starting a new upstream exchange, the system MUST honor a TERMINAL account status committed by a prior claim holder. When the fresh row's refresh-token fingerprint is UNCHANGED from the material the refresh was requested with (so no peer rotation repaired it) AND the fresh row's status is terminal (`REAUTH_REQUIRED` or `DEACTIVATED`) — for example a prior holder that hit a permanent `invalid_grant`, or the safe-terminal persist-conflict path that flags `REAUTH_REQUIRED` while leaving the consumed token stored — the system MUST NOT re-exchange that unchanged consumed/dead token; it MUST instead surface the terminal state as a PERMANENT refresh failure (fail closed), so a waiter that wins the released claim cannot blindly retry the consumed token and generate another permanent failure for an account a peer already removed from rotation. This decision MUST use the FRESH re-read status and fingerprint, never the stale selection snapshot, and MUST compose with the adopt-vs-exchange logic so that a CHANGED fingerprint (a peer genuinely re-authenticated/rotated and repaired the account) still causes the system to ADOPT the rotated stored tokens and proceed rather than treating a repaired account as terminal.

The claim release runs after the token update has been persisted (in a cleanup/`finally` path). A failure of the release itself (a transient DB error such as a SQLite lock past the busy timeout or a dropped Postgres connection) MUST NOT mask an otherwise successful refresh: the release MUST be retried briefly and then logged and suppressed, never propagated over a successful `_perform_refresh`/adoption return value, because the committed rotation is already durable and the stale claim harmlessly expires by its TTL. Suppression MUST be scoped to the release operation's own errors only: an exception raised by the refresh body itself MUST still propagate unchanged.

Claim ownership MUST be per-refresh, not process-wide: the stored claim identity MUST combine the claimant (replica/process) identity with a per-refresh owner token derived from the refresh-token material being exchanged (its fingerprint). The re-entrant same-owner takeover that lets a crashed refresh reclaim its own live claim MUST match only when BOTH the claimant AND the owner token are identical; a release MUST delete only the exact composed claim. Consequently, when two refreshes for the same account run in one process with different token fingerprints (for example a re-auth/import lands while an older forced refresh is still in flight), the second refresh MUST contend for the claim (wait until the first releases or the claim expires) rather than re-entering the first refresh's live claim, and neither refresh's release MAY delete the other's claim. The composed claim identity MUST fit the stored column width without truncating either the per-process suffix or the owner token.

After a successful upstream exchange, the system MUST persist the newly issued tokens with a compare-and-set conditioned on the refresh-token ciphertext observed in the immediately-preceding read. There MUST be NO unconditional token write anywhere in the persistence path: EVERY persist — including the final/exhaustion persist — MUST be a compare-and-set guarded on that observed ciphertext (`WHERE refresh_token_encrypted == :observed`). Because that comparison is atomic in the database, there is no read→write gap: if anything changed the row after the read (a non-deterministic re-encryption of the same plaintext OR a genuine peer rotation) the guarded write MISSES and clobbers nothing.

When that compare-and-set misses, the system MUST NOT assume any ciphertext change is a newer rotation: it MUST decide on the DECRYPTED refresh-token PLAINTEXT, never on the non-deterministic ciphertext (a concurrent re-authentication or import can re-encrypt the same plaintext to different bytes). It MUST re-read and compare the freshly observed stored plaintext against the plaintext this attempt exchanged FROM: (i) when the stored material is a genuinely different refresh token a peer rotated, so the system MUST adopt the stored row without persisting its own result and MUST NOT overwrite it; (ii) when the stored material is the same plaintext merely re-encrypted, the system MUST retry the ciphertext-guarded compare-and-set against the freshly observed ciphertext (bounded) so its own single-use rotation is persisted rather than discarded — it MUST NOT give up while the consumed token is still what is stored; (iii) only when the stored plaintext cannot be decrypted/compared MUST the system raise a transient (non-permanent) refresh error that is not recorded in the permanent-failure cooldown.

When the bounded guarded retries are exhausted without ever landing (a sustained same-plaintext re-encryption storm the system cannot win an atomic compare-and-set window against, with no genuinely different peer rotation observed) — OR the claim/caller deadline cut the retry loop mid-storm — the system MUST NOT immediately raise a transient error and drop the freshly rotated single-use token. It MUST first run a DEDICATED, small, bounded final-persist retry loop (a few guarded compare-and-set attempts with tiny backoff) that is DELIBERATELY SEPARATE from the claim/caller deadline: persisting a valid rotated token is worth a few extra milliseconds over budget, because giving up strands the account holding the already-consumed token. Each dedicated attempt is still a ciphertext-guarded compare-and-set keyed on the freshly re-read ciphertext (adopt a genuinely different peer plaintext, retry a same-plaintext re-encryption against the newly observed ciphertext); because any ciphertext change means a writer committed and no realistic writer re-encrypts the same consumed token in a tight loop, this lands within a couple of attempts in every realistic case. Only if those dedicated final retries are ALL exhausted while the stored material stays the already-consumed token (a truly pathological same-plaintext storm, or undecryptable stored material) does the system reach a SAFE TERMINAL OUTCOME: it MUST NOT surface a bare transient `token_persist_conflict` that releases the claim and lets a later blind retry re-exchange the still-stored consumed token into an `invalid_grant`/reauth PERMANENT knockout of an otherwise-healthy account. It MUST instead FAIL CLOSED by flagging the account `REAUTH_REQUIRED` through the SAME ciphertext-guarded status compare-and-set (keyed on the last-observed ciphertext), so the dead stored token is explicitly surfaced to operators (a recoverable, operator-visible state — the database genuinely holds a dead token) rather than left silently holding a consumed token that a blind retry would knock out; a genuine peer rotation that lands in the guard window is still ADOPTED (never clobbered), and only if even that guarded status write keeps missing on unchanged material through its own bounded budget MAY the system fall back to the transient (non-permanent) `token_persist_conflict` as the last resort (kept out of the permanent-failure cooldown). The system MUST NOT fall back to an unconditional write at any point.

Removing the unconditional write resolves — structurally, not by picking a side — the long-standing tension between never dropping the freshly rotated token and never clobbering a genuine peer rotation: (A) the freshly rotated token is not dropped, because when the stored plaintext is confirmed to be the same consumed token the system keeps pushing its new token in via the guarded retry (including the dedicated final-persist retries) rather than giving up; and (B) a genuine peer rotation is never clobbered, because every write is guarded, so a rotation that lands in the former read→write gap now simply causes a miss and is ADOPTED on re-read. The dedicated final-persist retries close the irreducible trilemma corner (never-clobber vs never-drop vs bounded-time) in every realistic case; the only residual outcome in the truly pathological corner is the SAFE TERMINAL `REAUTH_REQUIRED` flag (recoverable, never a permanent knockout, never a clobber), with the transient `token_persist_conflict` demoted to a last resort behind even the guarded status write.

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

#### Scenario: Process-default coordinator built before a pre-fork boundary

- **GIVEN** the process-default refresh-claim coordinator is constructed during preload/startup (before a pre-fork server forks its workers)
- **WHEN** two forked children each read their coordinator's claimant identity for the same account and refresh-token owner
- **THEN** each child yields a DISTINCT claimant identity and a distinct composed `claimed_by` (the auto-derived identity is resolved per OS process, never frozen at construction)
- **AND** a claimant identity explicitly injected by a caller stays unchanged across the fork
- **AND** repeated reads within one process return the same claimant identity

#### Scenario: Claim release failure does not mask a successful refresh

- **GIVEN** a replica won the refresh claim, completed the upstream exchange, and persisted the rotated tokens
- **WHEN** releasing the claim in the cleanup path raises a transient DB error (for example a SQLite lock past the busy timeout or a dropped Postgres connection)
- **THEN** the release is retried briefly and then logged and suppressed
- **AND** the caller still receives the successfully refreshed account (the release error never replaces the return value)
- **AND** the stale claim is left to expire by its TTL

#### Scenario: Claim release failure does not swallow a refresh-body error

- **GIVEN** a replica won the refresh claim and its upstream exchange raised a refresh error
- **WHEN** releasing the claim in the cleanup path also raises a transient DB error
- **THEN** the original refresh-body error propagates to the caller unchanged (the release error is suppressed, not the body error)

#### Scenario: Winner adopts a rotation that landed before its claim

- **GIVEN** a replica acquires the refresh claim for an account
- **AND** the freshly re-read refresh-token material differs from the material the refresh was requested with
- **WHEN** the replica proceeds
- **THEN** it returns the stored tokens without any upstream token exchange

#### Scenario: Waiter honors a prior holder's terminal status on an unchanged token

- **GIVEN** a prior claim holder finished by committing a terminal status (`REAUTH_REQUIRED` from a permanent `invalid_grant`, or the safe-terminal persist-conflict path) WITHOUT rotating `refresh_token_encrypted`, then released the claim
- **AND** a waiter subsequently wins the released claim with a stale snapshot of the same refresh token
- **WHEN** the waiter re-reads the account fresh and finds the refresh-token fingerprint UNCHANGED and the status terminal
- **THEN** it does NOT run a second upstream exchange of the consumed/dead token
- **AND** it surfaces the terminal state as a PERMANENT (non-transport) refresh failure, failing closed
- **AND** the account remains `REAUTH_REQUIRED` and the stored token is unchanged

#### Scenario: Waiter adopts a peer rotation that repaired a terminal account

- **GIVEN** a prior claim holder flagged the account `REAUTH_REQUIRED` on the old token
- **AND** a peer then genuinely re-authenticated, rotating `refresh_token_encrypted` (fingerprint changed) and clearing the status
- **WHEN** a waiter wins the claim, re-reads the account fresh, and finds the refresh-token fingerprint CHANGED
- **THEN** it adopts the peer's rotated stored tokens and proceeds without any upstream exchange
- **AND** it does NOT treat the repaired account as terminal

#### Scenario: Persistence compare-and-set misses on a re-encryption of the same token

- **GIVEN** a replica completed a successful upstream token exchange and holds the newly issued single-use tokens
- **AND** a concurrent re-authentication/import re-encrypted the SAME refresh-token plaintext to different ciphertext, so the persistence compare-and-set misses
- **WHEN** the replica re-reads the stored material and finds its refresh-token fingerprint unchanged from the material it exchanged
- **THEN** it retries the compare-and-set against the freshly observed ciphertext and persists its own newly issued tokens
- **AND** it does not adopt the re-encrypted, already-consumed token

#### Scenario: Persistence compare-and-set stabilizes on the second dedicated final-persist attempt

- **GIVEN** a replica completed a successful upstream token exchange and holds the newly issued single-use tokens
- **AND** the guarded persistence compare-and-set keeps missing on a same-plaintext re-encryption storm through the whole bounded retry budget AND the FIRST dedicated final-persist attempt
- **AND** the ciphertext then STABILIZES so the SECOND dedicated final-persist attempt's guarded compare-and-set (keyed on the last-observed ciphertext) can land
- **WHEN** the replica runs the dedicated final-persist retries (which are separate from the claim/caller deadline)
- **THEN** the second dedicated attempt persists the freshly rotated token and evicts the consumed one
- **AND** NO transient `token_persist_conflict` is raised, the token is not dropped, and the account is NOT flagged `REAUTH_REQUIRED`
- **AND** every attempt was a guarded compare-and-set, so nothing was clobbered

#### Scenario: Persistence compare-and-set never lands on a same-plaintext re-encryption storm

- **GIVEN** a replica completed a successful upstream token exchange and holds the newly issued single-use tokens
- **AND** the guarded persistence compare-and-set keeps missing on a sustained same-plaintext re-encryption storm until BOTH the bounded retry budget AND the dedicated final-persist retries are exhausted, with no genuinely different peer rotation ever observed
- **WHEN** the replica still cannot win an atomic compare-and-set window after the dedicated final-persist retries
- **THEN** it reaches the SAFE TERMINAL OUTCOME: it flags the account `REAUTH_REQUIRED` through the SAME ciphertext-guarded status compare-and-set (keyed on the last-observed ciphertext), so the account is explicitly surfaced for re-auth (recoverable, operator-visible — the database genuinely holds a dead, already-consumed token) rather than left silently holding a consumed token
- **AND** it MUST NOT surface a bare transient `token_persist_conflict` that releases the claim and lets a later blind retry re-exchange the still-stored consumed token into an `invalid_grant`/reauth PERMANENT knockout of the healthy account
- **AND** a genuine peer rotation observed while flagging is still ADOPTED (never clobbered), and only if even the guarded status write keeps missing on unchanged material through its own bounded budget MAY the transient `token_persist_conflict` be raised as a last resort (kept out of the permanent-failure cooldown)
- **AND** it never falls back to an unconditional write, so no write can clobber a rotation that lands in a read→write gap

#### Scenario: Persistence compare-and-set misses on a genuine peer rotation in the read→write gap

- **GIVEN** a replica completed a successful upstream token exchange and holds the newly issued single-use tokens
- **AND** its confirming re-read observed the same refresh-token plaintext it exchanged FROM (only re-encrypted)
- **AND** a genuinely different peer rotation lands AFTER that plaintext-confirming read but BEFORE the persist
- **WHEN** the replica issues its ciphertext-guarded write and it MISSES the peer's ciphertext, then re-reads and decrypts the stored plaintext and finds it is a genuinely different valid token
- **THEN** it adopts the peer's stored tokens without persisting its own result
- **AND** because the write was guarded it clobbered nothing, so the peer's newer valid tokens are never overwritten with the already-consumed material

#### Scenario: Persistence compare-and-set exhausts and the stored plaintext cannot be compared

- **GIVEN** a replica completed a successful upstream token exchange and holds the newly issued single-use tokens
- **AND** the persistence compare-and-set is exhausted and the stored refresh-token material cannot be decrypted for a plaintext comparison
- **WHEN** the replica cannot prove whether the stored material is the same consumed token or a genuine peer rotation
- **THEN** it raises a transient, non-permanent refresh error that is not recorded in the permanent-failure cooldown, so the caller retries the whole refresh once the contention clears rather than risking a clobber

#### Scenario: Persistence compare-and-set misses on a genuine peer rotation

- **GIVEN** a replica completed a successful upstream token exchange
- **AND** a peer committed a genuinely different refresh token, so the persistence compare-and-set misses
- **WHEN** the replica re-reads the stored material and finds its refresh-token fingerprint changed
- **THEN** it adopts the peer's stored tokens without persisting its own result

#### Scenario: Benign claim contention and post-exchange persist conflict are classified distinctly

- **GIVEN** a `RefreshError(code="refresh_claim_timeout", transport_error=True)` (benign: a peer holds the claim, no exchange happened) and a `RefreshError(code="token_persist_conflict", transport_error=True)` (post-exchange: the single-use token was consumed but its rotation could not be persisted)
- **WHEN** the classification predicates evaluate each
- **THEN** `is_refresh_claim_contention` is true ONLY for `refresh_claim_timeout`, `is_refresh_persist_conflict` is true ONLY for `token_persist_conflict`/`status_downgrade_conflict`, and `is_transient_refresh_contention` is true for BOTH
- **AND** a genuine `RefreshError(code="transport_error")` satisfies NONE of the three predicates
- **AND** both categories yield the same external outcome (retryable `upstream_unavailable`, never cached, no account-health penalty), but a post-exchange persist conflict is logged/observed distinctly from benign contention

#### Scenario: Retry after a post-exchange persist conflict re-exchanges rather than reusing the stored token

- **GIVEN** a refresh raised the transient `token_persist_conflict` (its `transport_error=True` keeps it out of the singleflight failure cache)
- **WHEN** the caller retries the refresh
- **THEN** the retry re-runs the WHOLE refresh (re-acquire the claim, fresh re-read, fresh upstream OAuth exchange) rather than reusing a cached result or reusing the possibly-consumed stored token
- **AND** the transient conflict MUST NOT be treated as an immediate permanent knockout without that fresh re-exchange attempt

### Requirement: Refresh claim losers wait bounded and never degrade account status

A process that fails to acquire the refresh claim MUST wait by polling within a bounded deadline (configurable cap, additionally bounded by the caller's refresh timeout budget). Each per-iteration poll sleep MUST be capped to the time remaining before that deadline (the smaller of the configured poll interval and the remaining budget), and when no time remains the loop MUST stop polling and fail fast with the transient claim-timeout error; a shielded refresh task MUST NOT sleep a full poll interval past the caller's deadline, because doing so would overrun the caller budget while still holding its repo session and the inflight singleflight entry that later callers join. When it observes rotated refresh-token material it MUST return the stored tokens without an upstream call. When the deadline elapses it MUST fail with a transient (non-permanent) refresh error that is not recorded in the permanent-failure cooldown, and it MUST NOT write `reauth_required` or `deactivated`, so token-refresh recovery fails over to another account instead of blocking.

When a process DOES win the claim (either immediately or after waiting on a foreign claim that released), and a caller refresh-timeout budget is in effect, the process MUST recompute the remaining budget (the caller's original deadline minus the elapsed wait) before starting the upstream OAuth exchange. Because the singleflight refresh body is shielded from caller cancellation and outlives a cancelled caller, it MUST NOT proceed into the exchange with the caller's ORIGINAL timeout budget still in force after a long wait: it MUST either fail fast with the transient (non-permanent) claim-timeout error when no budget remains, or cap the exchange timeout to the remaining budget, so a claim wait can never be followed by a full-budget exchange that overruns the request deadline and keeps the repo session and singleflight entry pinned past the budget.

The ENTIRE window during which the claim winner holds the cross-replica refresh claim MUST be bounded by the caller's remaining budget (when a budget is in effect), not merely the OAuth HTTP exchange. In particular, before the exchange the claim winner acquires token-refresh admission from the concurrency gate, and that admission acquire MUST be capped by the remaining budget: on a saturated token-refresh admission semaphore the wait for a slot (otherwise up to the configured admission wait timeout) MUST NOT exceed the caller's remaining budget while the claim is held. When the budget is already exhausted at admission time, or the admission wait would elapse it, the winner MUST fail fast with the transient (non-permanent) claim-timeout error (releasing the claim) rather than continuing to wait for a slot. After admission is acquired, the exchange-timeout cap MUST reflect the budget that actually remains (the admission wait counts against the budget), so admission wait plus exchange together cannot exceed the caller's budget and cannot hold the claim — blocking peer replicas — past the request deadline.

The POST-exchange persistence section — the token-persist compare-and-set loop and the permanent-failure status-downgrade compare-and-set loop — also runs while the claim is held, and MUST be bounded by a deadline (the smaller of the claim TTL and the caller's remaining budget), not by the compare-and-set attempt count alone. The FIRST guarded write of each loop MAY always run (the single-use token was already consumed upstream and must be persisted best-effort, and a genuine permanent failure must be recorded best-effort), but the loop MUST NOT keep RETRYING — and thus holding the claim — past that deadline: when the deadline passes mid-persist the system MUST stop and surface the transient (non-permanent) contention error (`token_persist_conflict` for the token persist, `status_downgrade_conflict` for the status downgrade), releasing the claim so the caller retries once contention clears, rather than looping until the attempt budget is exhausted. This keeps the TOTAL claim-hold (poll wait + admission + exchange + persist + release) within the caller budget plus a small fixed release, so a contended database write in the persist tail can never keep the claim held long enough for a peer replica to win the claim and re-exchange the already-consumed single-use refresh token.

The transient cross-replica refresh-contention `RefreshError` codes fall into TWO semantically distinct categories that MUST be classified separately, even though their EXTERNAL outcome is identical (all are `transport_error=True`, non-permanent, never cached in the permanent-failure cooldown, never record an account-health penalty, and fail over where applicable): (1) BENIGN CLAIM CONTENTION — `refresh_claim_timeout` — where a peer replica holds the account's refresh claim and THIS caller NEVER exchanged the token (the account's OAuth credentials are entirely healthy; pure contention); and (2) POST-EXCHANGE PERSIST/STATUS CAS CONFLICT — `token_persist_conflict` and `status_downgrade_conflict` — raised AFTER the upstream OAuth exchange when a guarded write lost a compare-and-set. For `token_persist_conflict` the single-use refresh token was already CONSUMED upstream but its rotation could not be persisted, so the database may still hold the just-consumed token; `status_downgrade_conflict` follows a permanent refresh failure whose guarded REAUTH status write lost a compare-and-set. The system MUST expose a narrow predicate recognizing ONLY category (1) (`is_refresh_claim_contention`), a distinct predicate recognizing ONLY category (2) (`is_refresh_persist_conflict`), and a union predicate recognizing BOTH (`is_transient_refresh_contention`). All proxy failover / skip-penalty paths MUST gate their unpenalized-retryable behavior on the UNION predicate (never on the broad `transport_error` flag), so both categories take the same external path; code that specifically means "a peer holds the claim and we did not exchange" MAY use the narrow predicate. A post-exchange persist/status CAS conflict MUST be logged/observed DISTINCTLY from benign claim contention (it signals a rarer, more-serious internal race worth surfacing in logs/metrics). Because a `token_persist_conflict` is not cached and remains non-permanent, a subsequent retry MUST re-run the WHOLE refresh — a fresh upstream re-exchange — rather than reusing the possibly-consumed stored token; the retry MUST NOT treat the transient conflict as an immediate permanent knockout.

When a proxy stream turn NOT hard-pinned to a required account encounters this transient cross-replica refresh-contention failure (`is_transient_refresh_contention` — ONLY the `refresh_claim_timeout`, `status_downgrade_conflict`, and `token_persist_conflict` codes, NOT the broad `transport_error` flag), the streaming retry loop MUST exclude the affected account and fail over to a different account rather than reselecting the claimed account until attempts are exhausted, WITHOUT recording an account-health penalty (its credentials are healthy; only its refresh claim is held by a peer replica). This claim-contention failover MUST apply to both the proactive freshness check on the first stream attempt (before any upstream 401) and the forced refresh on the post-401 recovery attempt. Before failing over, the loop MUST release the stream lease it already acquired for the skipped account so that account does not continue to consume one of its stream-concurrency slots for a stream that will never open. On this transient-claim failover the loop MUST also record a retryable `upstream_unavailable` stream error (mirroring the transient aiohttp/connect failover and the WebSocket connect loop): when EVERY candidate account hits a transient refresh-claim timeout before the stream opens and attempts are exhausted, the client MUST receive the temporary `upstream_unavailable` (retryable/capacity) condition rather than a misleading generic `no_accounts` response. A GENUINE OAuth transport failure (a `RefreshError` with `code == "transport_error"`) on either the freshness check or the post-401 forced refresh is NOT claim contention: it MUST NOT take this unpenalized failover path but MUST be handled identically to a connect transport failure — recording the account-health penalty via `_handle_stream_error` and gating failover on the message text — so a persistently broken account is pushed into transient backoff instead of being kept healthy and reselected on the next request. On this movable transport-failure failover the loop MUST also release the skipped account's already-acquired stream lease (setting it to `None`) BEFORE the failover `continue`, symmetrically on BOTH the proactive freshness check and the post-401 forced refresh, matching the claim-contention and permanent-failure branches; otherwise the excluded account keeps holding one of its stream-concurrency slots for the entire duration of the replacement stream.

When a proxy stream turn's proactive freshness check or post-401 forced (`force=True`) refresh raises a PERMANENT `RefreshError` (not a transient claim contention), the streaming retry loop MUST mark the account permanently failed (removing it from selection) AND MUST release the account's already-acquired stream lease BEFORE failing over to the next candidate. Marking the account failed removes it from future selection but does not itself free the stream-concurrency slot the lease occupies; because the failover streams on a different account for the remaining request duration, an unreleased lease would keep the dead account's slot held for that entire duration. This lease release MUST apply symmetrically to BOTH the proactive freshness check on the first stream attempt (before any upstream 401) AND the post-401 forced refresh, matching the transient branches' immediate release at failover.

When a proxy stream turn IS hard-pinned to a required account — a session-continuity `previous_response_id` bound to a preferred account or a file-required preferred account, which sets `preferred_account_id` (and, for `previous_response_id`, `require_preferred_account`) — the movable failover above is correctly skipped so the request never crosses accounts (preserving the account-ownership invariant). But the streaming retry loop MUST NOT then fall through to an unconditional reselect that reselects the same pinned account until attempts are exhausted: on a cross-replica refresh-CLAIM-CONTENTION failure (`is_transient_refresh_contention`) for a hard-pinned stream, the loop MUST release the pinned account's already-acquired stream lease (no leaked slot) and MUST surface a retryable `upstream_unavailable` error promptly rather than spinning pointlessly on the held claim and then surfacing a misleading `no_accounts` result. This hard-pinned handling MUST apply symmetrically to BOTH the proactive freshness check on the first stream attempt (before any upstream 401) AND the forced (`force=True`) refresh on the post-401 recovery attempt, so a hard-pinned stream that opens, receives a 401, and then hits a claim-contention timeout on its forced refresh also stays on the owner, releases the lease, and surfaces the retryable `upstream_unavailable` promptly instead of reselecting the same owner until exhaustion. The transient claim contention MUST NOT be recorded as a permanent failure. A GENUINE OAuth transport failure (a `RefreshError` with `code == "transport_error"`, NOT claim contention) on either the freshness check or the post-401 forced refresh MUST NOT take this unpenalized claim-contention path: it MUST be handled identically to a connect transport failure (recording the account-health penalty via `_handle_stream_error`) so a persistently broken account backs off instead of being kept healthy and reselected. This does not apply to a locally verified cross-transport fresh-replay body, which may still move off the failed owner as specified elsewhere.

The WebSocket connect loop MUST apply the same failover for a transient, cross-replica refresh-CLAIM-CONTENTION failure (`is_transient_refresh_contention` — ONLY the `refresh_claim_timeout`, `status_downgrade_conflict`, and `token_persist_conflict` codes, NOT the broad `transport_error` flag) reaching the connect path (on both the proactive freshness check and the post-401 forced refresh): rather than surfacing a bogus 401 `invalid_api_key`, it MUST release the skipped account's already-acquired stream lease, exclude the account, and reselect a healthy account WITHOUT recording an account-health penalty. A GENUINE OAuth transport failure (a `RefreshError` with `code == "transport_error"`, NOT claim contention) on either the freshness check or the post-401 forced refresh MUST NOT take this unpenalized claim-contention path (and MUST NOT surface a terminal 401 `invalid_api_key`): it MUST be treated identically to a connect transport failure — raising a retryable `upstream_unavailable` so the connect loop's normal transport-failure failover/health handling (`_handle_websocket_connect_error`, which records the account-health penalty) applies — so a persistently broken account backs off instead of being kept healthy and reselected. This claim-contention failover MUST be gated only on whether the request is *hard-pinned to a required account* — that is, session-continuity (a `previous_response_id` bound to a preferred account) or a file-required preferred account; it MUST NOT be suppressed merely because a *soft* preferred account is set. In particular, a forced-refresh reconnect auth replay sets the stale account as both the forced-refresh target and the preferred account, but a movable request (no session continuity, no file pin) MUST still exclude the stale account and fail over on a transient transport claim failure. A hard-pinned request MUST stay on its required account (never crossing accounts, never marking a permanent failure), preserving the account-ownership invariant for session-continuity and file-pinned requests; but because the pinned owner's credentials are healthy (its refresh claim is merely held by a peer replica), the connect path MUST NOT surface a terminal 401 `invalid_api_key` for the transient (transport-level / non-permanent) claim failure — it MUST instead release the pinned account's already-acquired stream lease and surface a RETRYABLE `upstream_unavailable` connect failure so the client can retry once the peer replica releases the claim. This hard-pinned handling MUST apply symmetrically to BOTH the proactive freshness check on the connect attempt (before any upstream 401) AND the post-401 forced (`force=True`) refresh recovery attempt. Permanent or non-transport refresh failures keep the terminal 401 `invalid_api_key`. When every account attempt is exhausted by such transient claim failovers, the connect loop MUST emit a proper terminal error to the client (a 503/capacity-style upstream error, not a 401 `invalid_api_key` and not a silent no-op that leaves the client waiting).

The compact-responses path MUST apply the same failover for a transient, cross-replica refresh-CLAIM-CONTENTION failure raised on BOTH its proactive `_ensure_fresh_with_budget` freshness-check preflight AND the post-401 forced (`force=True`) refresh recovery attempt: rather than letting the non-permanent `RefreshError` escape unhandled on the preflight (which surfaces to the client as an unhandled server error) or re-raising the original upstream 401 on the post-401 recovery (which surfaces a misleading `invalid_api_key`), it MUST retain a retryable `upstream_unavailable` error, exclude the account, and reselect a healthy account within the compact account-attempt loop. As on the previsible-unary path, this no-account-health-penalty behavior MUST be gated on the PRECISE claim-contention predicate (`is_transient_refresh_contention`) recognizing ONLY the `refresh_claim_timeout`, `status_downgrade_conflict`, and `token_persist_conflict` codes, and MUST NOT be gated on the broad `transport_error` flag alone. The preflight branch MUST additionally release the selected account's `response_create` lease before failover. Because peer-claim contention is not the account's fault (its credentials are healthy; only its refresh claim is held by another replica), this transient-claim failover MUST NOT record an account-health penalty (it MUST NOT call `record_error` / mark the account unhealthy), matching the streaming and WebSocket paths, which only release and exclude the account. Genuine transport-level failures on the compact path — both a `RefreshError` with `code == "transport_error"` (the OAuth refresh request itself timing out / its upstream connection failing) AND raw aiohttp/connect errors, which are NOT refresh-claim contention — MUST retain their existing account-health accounting: they are handled identically to a connect transport failure (gate failover on the message text and `record_error` via `_handle_stream_error` on the skipped account) so a persistently broken account is pushed into transient backoff instead of being kept healthy and reselected on the next request. When the request is pinned to a preferred account, both claim-contention branches MUST instead surface a retriable upstream-unavailable error on that account rather than crossing to another account. On the HTTP bridge / forwarded compact path the caller passes an `api_key_reservation_override` with `owns_reservation` false, making `compact_responses` responsible for finalizing that API-key reservation; therefore EVERY terminal raise in the preflight (proactive-freshness) exception handler MUST settle the compact API-key usage reservation (release it via `_settle_compact_api_key_usage`) BEFORE raising, symmetrically with the post-401 forced-refresh block. This covers not only BOTH pinned transient-claim branches (preflight and post-401 forced refresh), but ALSO the preflight's genuine-transport-error terminal raises (the non-retryable `_raise_proxy_unavailable` and the pinned-transport `_raise_proxy_unavailable`) AND the preflight's permanent-`RefreshError` re-raise, so a file/previous-response-pinned compact whose refresh fails on the preflight — for claim contention, a genuine OAuth transport error, OR a permanent failure — never leaves the reservation unfinished holding API-key quota. (A movable transport-error failover that `continue`s to the next account correctly does NOT settle, because the reservation is carried to the retry.) When EVERY candidate account hits the transient claim timeout and the account-attempt loop is exhausted, the client MUST receive the retained retryable `upstream_unavailable` error rather than the misleading original 401. A permanent or non-transport refresh failure MUST keep its prior escalation (it propagates to the caller) rather than being reinterpreted as a transient failover.

The previsible-unary failover path (`_ensure_previsible_unary_fresh_with_failover`, which serves movable previsible-unary requests such as thread-goal, codex-control, transcription, and file operations) MUST apply the same failover for a transient, cross-replica refresh-CLAIM-CONTENTION failure raised on its proactive `_ensure_fresh_with_budget` freshness check: it MUST exclude the affected account and fail over to a healthy account. The no-account-health-penalty behavior MUST be gated on a PRECISE claim-contention predicate (`is_transient_refresh_contention`) that recognizes ONLY the cross-replica claim/CAS `RefreshError` codes this change introduces — `refresh_claim_timeout`, `status_downgrade_conflict`, and `token_persist_conflict` — and MUST NOT be gated on the broad `transport_error` flag alone, because `refresh_access_token` also raises `RefreshError(code="transport_error", transport_error=True)` for a GENUINE OAuth transport failure (the OAuth refresh request itself timing out / its upstream connection failing). Because peer-claim contention is not the account's fault (its credentials are healthy; only its refresh claim is held by another replica), this transient-claim failover MUST NOT record an account-health penalty (it MUST NOT call `record_error` / `_handle_stream_error` for the skipped account), matching the streaming, WebSocket, and compact paths. This transient-claim failover is definitionally transient, so it ALWAYS fails over rather than gating on the message text. Genuine transport-level refresh failures on this path — both a `RefreshError` with `code == "transport_error"` AND raw aiohttp/connect errors, which are NOT refresh-claim contention — MUST retain their existing account-health accounting: they gate failover on the message text and `record_error` (via `_handle_stream_error`) the failed account so a persistently broken account is pushed into transient backoff instead of being reselected on the next request. When the request is strict-pinned to a required account, or when every candidate account is exhausted, a claim-contention failure MUST surface a retryable `upstream_unavailable` error WITHOUT recording a health penalty on the last claim-held account (its caller's terminal error handler MUST recognize the claim-contention-derived `upstream_unavailable` and skip the penalty), so pure cross-replica contention never pushes an otherwise-healthy account into backoff; a genuine transport failure under the same strict-pin/exhaustion condition MUST keep its terminal health penalty.

#### Scenario: Previsible-unary freshness-check claim timeout fails over without a health penalty

- **GIVEN** a movable previsible-unary request (for example a thread-goal request, not pinned to a required account) whose first-selected account is stale and needs a proactive refresh
- **AND** that account's refresh claim is held by another replica past the wait cap
- **WHEN** the freshness-check preflight raises the transient, transport-level claim error
- **THEN** the previsible-unary failover loop excludes that account and fails over to a healthy account
- **AND** the client receives a normal response served by the healthy account
- **AND** the excluded (claim-held) account is not penalized with a transient account-health error (`record_error` / `_handle_stream_error` is not called) for the peer-claim contention
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Previsible-unary exhausts every account on transient claim failovers without penalty

- **GIVEN** a movable previsible-unary request not pinned to a required account
- **AND** every candidate account's refresh claim is held by another replica so its freshness check raises the transient claim error
- **WHEN** the previsible-unary failover loop excludes each account and exhausts its attempts
- **THEN** the client receives a retryable `upstream_unavailable` error rather than a generic `no_accounts` response
- **AND** no account (including the last one attempted) is penalized with a transient account-health error (`record_error` is not called) for the peer-claim contention
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Previsible-unary genuine transport-error refresh retains its health penalty

- **GIVEN** a movable previsible-unary request (for example a thread-goal request, not pinned to a required account) whose first-selected account is stale and needs a proactive refresh
- **AND** that account's proactive refresh fails with a GENUINE OAuth transport failure — a `RefreshError` with `code == "transport_error"` (the OAuth refresh request itself timed out / its upstream connection failed), NOT cross-replica claim contention
- **WHEN** the freshness-check preflight raises that transport-level `RefreshError`
- **THEN** the previsible-unary failover loop excludes that account and fails over to a healthy account
- **AND** the excluded account IS penalized with a transient account-health error (`record_error` via `_handle_stream_error` is called) so the broken account is pushed into transient backoff instead of being reselected on the next request
- **AND** when the request is strict-pinned to that account (or every candidate is exhausted) the terminal `upstream_unavailable` still records the account-health penalty rather than skipping it as claim contention

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

#### Scenario: Claim wait consumes the caller budget before the exchange

- **GIVEN** a caller refresh-timeout budget and a foreign refresh claim that is held for nearly the whole budget and then releases
- **WHEN** the waiting replica wins the claim after the wait and the material has not rotated
- **THEN** it recomputes the remaining budget before the upstream OAuth exchange
- **AND** it fails fast with the transient (non-permanent) claim-timeout error when no budget remains, rather than starting a full-budget exchange that overruns the request deadline
- **AND** when some budget remains it caps the exchange timeout to that remaining budget

#### Scenario: Admission wait is bounded by the remaining caller budget

- **GIVEN** a caller refresh-timeout budget and a claim winner whose token-refresh admission semaphore is fully saturated (no slot available within the budget)
- **WHEN** the winner tries to acquire token-refresh admission before the upstream OAuth exchange
- **THEN** the admission wait is capped by the remaining budget rather than the full configured admission wait timeout
- **AND** it fails fast with the transient (non-permanent) claim-timeout error and RELEASES the claim within approximately the remaining budget, rather than holding the claim for the full admission timeout and blocking peer replicas

#### Scenario: Claim poll sleep is bounded by the remaining caller budget

- **GIVEN** a caller refresh-timeout budget smaller than the configured poll interval and a live foreign refresh claim that never releases within the budget
- **WHEN** the losing replica polls for the claim to clear
- **THEN** each poll sleep is capped to the smaller of the poll interval and the time remaining before the deadline
- **AND** the loser fails with the transient (non-permanent) claim-timeout error bounded by the caller budget rather than sleeping a full poll interval past the deadline while pinning its repo session and singleflight entry

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

#### Scenario: Permanent proactive-refresh failure releases the stream lease before failover

- **GIVEN** a movable proxy stream turn whose first-selected account's proactive freshness check raises a PERMANENT `RefreshError`
- **WHEN** the streaming retry loop marks the account permanently failed and fails over
- **THEN** the account's already-acquired stream lease is released BEFORE the failover streams on the replacement account
- **AND** the failed account never serves the stream while a healthy alternate does

#### Scenario: Permanent post-401 forced-refresh failure releases the stream lease before failover

- **GIVEN** a movable proxy stream turn that opens on its account, receives an upstream 401, and whose forced (`force=True`) refresh then raises a PERMANENT `RefreshError`
- **WHEN** the streaming retry loop marks the account permanently failed and fails over
- **THEN** the account's already-acquired stream lease is released BEFORE the failover streams on the replacement account
- **AND** the failed account never serves the replacement stream while a healthy alternate does

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

#### Scenario: Stream genuine transport-error refresh retains its health penalty

- **GIVEN** a movable proxy stream turn (not hard-pinned) whose selected account needs a refresh
- **AND** that account's refresh fails with a GENUINE OAuth transport failure — a `RefreshError` with `code == "transport_error"` (the OAuth refresh request itself timed out / its upstream connection failed), NOT cross-replica claim contention
- **WHEN** the streaming retry loop evaluates the failure on either the proactive freshness check or the post-401 forced refresh
- **THEN** the loop records the account-health penalty (`record_error` via `_handle_stream_error`) on the skipped account (unlike a claim-contention timeout, which is not penalized) and fails over to a healthy account
- **AND** the failed account is pushed into transient backoff instead of being kept healthy and reselected on the next request
- **AND** the failed account's already-acquired stream lease is released BEFORE the replacement account streams, so its stream-concurrency slot is not held for the duration of the replacement stream

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

#### Scenario: Hard-pinned WebSocket connect stays on its owner and returns retryable error on freshness-check claim timeout

- **GIVEN** a hard-pinned WebSocket responses request (session-continuity `previous_response_id` bound to a preferred account, which sets `preferred_account_id` and `require_preferred_account`)
- **AND** the pinned owner account's refresh claim is held by another replica so its proactive connect-path freshness check raises the transient, transport-level claim error before the upstream websocket opens
- **WHEN** the connect path evaluates the transient claim failure for the pinned request
- **THEN** the connect loop does NOT cross to another account (the account-ownership invariant is preserved)
- **AND** the pinned account's already-acquired stream lease is released (not leaked)
- **AND** the client receives a RETRYABLE `upstream_unavailable` connect failure rather than a terminal 401 `invalid_api_key`
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Hard-pinned reconnect stays on its required account and returns retryable error on post-401 forced-refresh claim timeout

- **GIVEN** a hard-pinned WebSocket responses request (session-continuity `previous_response_id` bound to a preferred account, or a file-required preferred account)
- **AND** a reconnect auth replay has set that required account as the forced-refresh target
- **WHEN** the post-401 forced refresh on that account raises the transient, transport-level claim error
- **THEN** the connect loop does NOT cross to another account
- **AND** the pinned account's already-acquired stream lease is released (not leaked)
- **AND** the client receives a RETRYABLE `upstream_unavailable` connect failure rather than a terminal 401 `invalid_api_key`
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: WebSocket connect exhausts every account on transient claim failovers

- **GIVEN** a WebSocket responses connection not pinned to a preferred/required account
- **AND** every account attempt (up to the WebSocket max-account-attempts) raises the transient, transport-level claim error
- **WHEN** the connect loop excludes each account and exhausts its attempts
- **THEN** the client receives a proper terminal error frame (a 503/capacity-style upstream error), not a 401 `invalid_api_key` and not a silent no-op
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: WebSocket genuine transport-error refresh retains its health penalty

- **GIVEN** a WebSocket responses connection whose selected account needs a refresh
- **AND** that account's refresh fails with a GENUINE OAuth transport failure (a `RefreshError` with `code == "transport_error"`), NOT cross-replica claim contention
- **WHEN** the connect path evaluates the failure on either the proactive freshness check or the post-401 forced refresh
- **THEN** the connect path raises a retryable `upstream_unavailable` routed through the connect-error penalty/failover path (`_handle_websocket_connect_error`, which records the account-health penalty) rather than the unpenalized `_WebSocketTransientRefreshFailover` claim-contention path or a terminal 401 `invalid_api_key`
- **AND** the failed account is penalized and the request fails over to a healthy account
- **AND** the genuine transport failure is never recorded as a permanent failure

#### Scenario: Compact freshness-check claim timeout fails over instead of erroring out

- **GIVEN** a compact-responses request whose first-selected account is stale and needs a proactive refresh
- **AND** that account's refresh claim is held by another replica past the wait cap
- **WHEN** the freshness-check preflight raises the transient, transport-level claim error
- **THEN** the compact account-attempt loop releases the account's `response_create` lease, excludes that account, and fails over to a healthy account
- **AND** the client receives a normal compact response rather than an unhandled server error
- **AND** the transient claim contention is never recorded as a permanent failure
- **AND** the excluded account is not penalized with a transient account-health error (`record_error` is not called) for the peer-claim contention

#### Scenario: Compact post-401 forced-refresh claim timeout fails over instead of surfacing 401

- **GIVEN** a compact-responses request not pinned to a preferred account whose selected account returns an upstream 401
- **AND** the post-401 forced (`force=True`) refresh raises the transient, transport-level claim error because the claim is held by another replica
- **WHEN** the compact account-attempt loop retains a retryable `upstream_unavailable`, excludes that account, and fails over to a healthy account
- **THEN** the client receives a normal compact response rather than the misleading original 401
- **AND** the excluded account is not penalized with a transient account-health error (`record_error` is not called) for the peer-claim contention
- **AND** when every candidate account hits the transient claim timeout and attempts are exhausted, the client receives the retryable `upstream_unavailable` error rather than the 401
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Compact genuine transport-error refresh retains its health penalty

- **GIVEN** a compact-responses request (not pinned) whose selected account needs a refresh
- **AND** that account's refresh fails with a GENUINE OAuth transport failure (a `RefreshError` with `code == "transport_error"`), NOT cross-replica claim contention, on either the freshness-check preflight or the post-401 forced refresh
- **WHEN** the compact account-attempt loop evaluates the failure
- **THEN** it records the account-health penalty (`record_error` via `_handle_stream_error`) on the skipped account (unlike a claim-contention timeout, which is not penalized) and fails over to a healthy account
- **AND** when every candidate account hits the genuine transport failure and attempts are exhausted, the client receives a retryable `upstream_unavailable` error

#### Scenario: Pinned compact refresh-claim timeout settles the API-key reservation before raising

- **GIVEN** a file/previous-response-pinned compact-responses request invoked through the HTTP bridge / forwarded path with an `api_key_reservation_override` and `owns_reservation` false
- **AND** the pinned owner account's refresh claim is held by another replica so its freshness-check preflight (or post-401 forced refresh) raises the transient, transport-level claim error
- **WHEN** the compact account-attempt loop surfaces the retryable `upstream_unavailable` for the pinned request (which cannot cross accounts)
- **THEN** the compact API-key usage reservation is settled (released via `_settle_compact_api_key_usage`) before the error is raised, so it does not leak held API-key quota
- **AND** the client receives a retryable `upstream_unavailable` error rather than an unhandled server error

### Requirement: Refresh-path sibling writes never clobber a peer rotation, and the warmup path honors the claim-contention taxonomy

The no-unconditional-write and no-clobber guarantees MUST hold across the WHOLE write surface reachable on the refresh/`ensure_fresh` hot path, not only inside the three rewritten helpers. This invariant MUST be enforced STRUCTURALLY at the repository (data) layer so no current or future caller can reopen the clobber class:

Refresh-token ciphertext writes MUST be compare-and-set at the repository layer. The accounts repository MUST expose exactly ONE method that writes access/refresh/id token ciphertext (`rotate_tokens`), and that method MUST take a REQUIRED (non-optional) `expected_refresh_token_encrypted` compare-and-set predicate — there MUST be no parameter combination that writes `refresh_token_encrypted` unconditionally. Metadata writes MUST NOT touch token material: the repository MUST expose a separate metadata-only method (`update_account_metadata`) for identity/plan/workspace fields (`chatgpt_account_id`, `chatgpt_user_id`, `plan_type`, `email`, `workspace_id`, `workspace_label`, `seat_type`, `last_refresh`) that STRUCTURALLY cannot write token ciphertext (it has no parameter for it). Consequently a metadata-only writer holding a stale `Account` snapshot — loaded before a peer replica's guarded refresh — can never clobber a concurrent rotation, because the only code path that touches `refresh_token_encrypted` is the mandatory compare-and-set in `rotate_tokens`. Every caller MUST route to the correct method: token-rotation callers (the refresh persist and permanent-failure paths) to the guarded `rotate_tokens`, and metadata-only callers (the `chatgpt_account_id` backfill and the usage identity/plan/workspace sync) to `update_account_metadata`.

The `chatgpt_account_id` backfill (`_ensure_chatgpt_account_id`), which runs on every `ensure_fresh` — including the fast no-refresh path — for a legacy account still missing its `chatgpt_account_id`, MUST persist the derived `chatgpt_account_id` through the metadata-only writer, which structurally cannot touch token material. Its caller-time in-memory selection snapshot is not re-read under a claim, so routing the backfill through a token-writing method would risk clobbering a concurrent peer rotation of the single-use token that lands in the read→write window with already-consumed material; the metadata-only path removes that risk entirely (a concurrent rotation is simply not observable to this write, and the derived id is persisted without ever reading or writing the refresh-token ciphertext). No `ensure_fresh` path may perform an unconditional token write, and no metadata-only path may write token ciphertext at all.

The post-exchange token persist MUST NOT drop a freshly rotated token on a compare-and-set miss. After the upstream OAuth exchange has succeeded (the old single-use refresh token consumed, a new one minted), a compare-and-set miss — whether the bounded same-plaintext re-encryption retries are exhausted OR the claim/caller deadline cuts the retry loop — MUST NOT raise a transient `token_persist_conflict` in place of attempting to persist the new token. On any such miss the newly-rotated token MUST get a DEDICATED, small, bounded final-persist retry loop (a few guarded compare-and-set attempts with tiny backoff) keyed on the LAST-OBSERVED ciphertext, DELIBERATELY SEPARATE from the claim/caller deadline (persisting a valid single-use rotated token is worth a few extra milliseconds over budget); because each attempt is itself a compare-and-set it lands only when nothing changed since the read (no clobber) and misses harmlessly otherwise. On each miss the system MUST re-read once more and decide on the decrypted plaintext: a genuinely different stored plaintext means a peer rotation legitimately superseded ours and MUST be ADOPTED (never overwritten); the same plaintext merely re-encrypted MUST be RETRIED against the newly observed ciphertext within the dedicated budget; undecryptable material stops the dedicated retries. Because any ciphertext change means a writer committed and no realistic writer re-encrypts the same consumed token in a tight loop, the dedicated retries land the rotation within a couple of attempts in every realistic case. Only if the dedicated final retries are ALL exhausted while the stored material stays the already-consumed token does the system reach a SAFE TERMINAL OUTCOME: it MUST NOT drop the rotation behind a bare transient `token_persist_conflict` that a later blind retry would turn into an `invalid_grant`/reauth PERMANENT knockout of a healthy account, and MUST instead FAIL CLOSED by flagging the account `REAUTH_REQUIRED` through the SAME ciphertext-guarded status compare-and-set (adopting a genuine peer rotation observed in the guard window, never clobbering it; demoting the transient conflict to a last resort only if even the guarded status write cannot land). The deadline therefore bounds the RETRY LOOP and the network/admission waits, never the dedicated final-persist retries or the safe terminal flag — a crashed-storm retry budget can never be the reason a freshly minted token is dropped while the database keeps a consumed one, nor the reason a healthy account is permanently knocked out.

The permanent-status downgrade MUST have a SINGLE guarded authority. `AuthManager` (`_handle_permanent_refresh_failure`) owns the primary refresh-token-ciphertext-guarded compare-and-set. The proxy load balancer's `mark_permanent_failure` MUST NOT perform an UNGUARDED database status write on the permanent-refresh-failure path: its persistence MUST route through a compare-and-set conditioned on the account's refresh-token ciphertext (`update_status_if_current` with `expected_refresh_token_encrypted`), so a concurrent peer re-authentication/import rotation causes a MISS instead of clobbering the peer's repaired `ACTIVE`/rotated row back to `reauth_required` (and tearing down its sticky/bridge sessions). A genuine permanent failure MUST still result in exactly ONE guarded database downgrade: in the single-caller case `AuthManager` has already CAS-written the downgrade and mutated the in-memory object, so the load balancer's guarded write is predicate-skipped as redundant; the load balancer's guarded write covers only the callers whose in-memory object did not go through that CAS (an intra-process singleflight joiner sharing the winner's permanent error) and non-refresh permanent failures. No path may perform an unguarded status downgrade write on the refresh-permanent-failure path. The local routing overlay MUST honor the guarded-CAS result: `mark_permanent_failure` MUST mark the account routing-unavailable in this replica's local overlay ONLY when the guarded downgrade actually applied (the compare-and-set landed, or no write was needed because the primary authority already CAS-wrote it). When the compare-and-set MISSES because a peer replica repaired/rotated the row (the database row is left `ACTIVE`), the caller MUST NOT mark the account routing-unavailable — excluding a freshly repaired healthy account from local routing would be a self-inflicted routing loss that undermines the CAS guard — so the account remains selectable in this replica.

The proxy warmup submit path MUST classify a refresh failure with the same taxonomy as the core proxy request paths: a transient cross-replica refresh-CLAIM-CONTENTION failure (`is_transient_refresh_contention` — the `refresh_claim_timeout`, `status_downgrade_conflict`, and `token_persist_conflict` codes) MUST surface as a retryable `upstream_unavailable` in the warmup result and request log, NOT as `invalid_api_key`, because the account's OAuth credentials are healthy (only its refresh claim is held by a peer replica). A permanent `RefreshError` keeps its `invalid_api_key` classification (and marks the permanent failure), and a genuine non-contention transport-level `RefreshError` also keeps `invalid_api_key`.

#### Scenario: Legacy chatgpt_account_id backfill routes through the metadata-only writer

- **GIVEN** a legacy account whose `chatgpt_account_id` is unset but whose stored id-token yields a derivable `chatgpt_account_id`
- **AND** the account's token material is fresh, so `ensure_fresh` takes the no-refresh fast path straight into the backfill
- **WHEN** `ensure_fresh` runs and persists the derived `chatgpt_account_id`
- **THEN** the write goes through the metadata-only repository method, which has no parameter for token ciphertext and therefore never reads or writes `refresh_token_encrypted`
- **AND** a concurrent peer rotation of the single-use refresh token is untouched, because the backfill is structurally incapable of writing token material

#### Scenario: Repository refuses an unguarded refresh-token write

- **GIVEN** the accounts repository's token-writing method (`rotate_tokens`)
- **WHEN** any caller attempts to persist token ciphertext
- **THEN** the method requires a non-optional `expected_refresh_token_encrypted` compare-and-set predicate, so there is no code path that writes `refresh_token_encrypted` unconditionally
- **AND** a concurrent rotation committed after the caller read the expected ciphertext turns a stale writer into a guarded MISS (no write, no clobber), never an unconditional overwrite

#### Scenario: Metadata write cannot touch token material

- **GIVEN** the accounts repository's metadata-only method (`update_account_metadata`)
- **WHEN** an identity/plan/workspace sync writes account metadata from a stale in-memory snapshot
- **THEN** the method has no parameter for access/refresh/id token ciphertext and persists only metadata columns
- **AND** the stored token material is left exactly as it was, so a concurrent refresh-token rotation is never clobbered by a metadata write

#### Scenario: Proxy permanent-failure mark does not clobber a peer's rotated repair

- **GIVEN** a proxy caller holds a stale in-memory account object (still `ACTIVE`, holding the OLD refresh-token ciphertext that just failed permanently) — for example an intra-process singleflight joiner that received the winner's re-raised permanent `RefreshError`
- **AND** a peer replica has already re-authenticated/rotated that account in the database to `ACTIVE` with a freshly rotated refresh token
- **WHEN** the proxy calls `mark_permanent_failure` for the account
- **THEN** the guarded status compare-and-set (conditioned on the old refresh-token ciphertext) MISSES the rotated ciphertext and performs no write
- **AND** the peer's repaired `ACTIVE`/rotated row is NOT clobbered back to `reauth_required` and its sessions are not torn down
- **AND** the caller MUST NOT mark the account routing-unavailable in this replica's local overlay, so the freshly repaired `ACTIVE` account remains selectable here

#### Scenario: Proxy permanent-failure mark still downgrades when no peer rotation occurred

- **GIVEN** a genuine permanent refresh failure with no concurrent peer rotation (the in-memory refresh-token ciphertext matches the stored row)
- **WHEN** the proxy calls `mark_permanent_failure` for the account
- **THEN** the single guarded status compare-and-set lands and the account is downgraded to `reauth_required`
- **AND** the account IS marked routing-unavailable in this replica's local overlay (excluded from local selection), because the permanent downgrade actually applied

#### Scenario: Post-exchange persist runs dedicated final retries when the deadline cuts the retry loop

- **GIVEN** a claim winner completed the upstream exchange (new refresh token minted, old one consumed) and enters the token-persist compare-and-set loop while holding the claim
- **AND** the guarded compare-and-set keeps missing on a sustained same-plaintext re-encryption storm while the claim/caller deadline has already passed
- **WHEN** the deadline cuts the retry loop
- **THEN** the loop stops retrying but STILL runs the DEDICATED, bounded final-persist retries keyed on the last-observed ciphertext (which are separate from the deadline), NOT the full attempt budget, and NEVER an unconditional write
- **AND** only if those dedicated final retries are ALL exhausted while the stored material stays the already-consumed token does the persist reach the SAFE TERMINAL OUTCOME, flagging the account `REAUTH_REQUIRED` through the guarded status compare-and-set rather than dropping the rotation behind a bare transient `token_persist_conflict` that a later blind retry would turn into a permanent knockout
- **AND** the claim is released so the total claim-hold stays within the caller budget plus the small fixed dedicated-retry headroom and release

#### Scenario: Deadline-cut persist lands the rotated token when the stored plaintext is unchanged

- **GIVEN** a claim winner completed the upstream exchange and the claim/caller deadline has already elapsed
- **AND** the stored refresh-token plaintext is still exactly the consumed token (only re-encrypted), so the first guarded write missed on the shifted ciphertext
- **WHEN** the deadline cuts the retry loop and the final ciphertext-guarded persist runs against the last-observed ciphertext
- **THEN** the final guarded write lands the freshly rotated token, the database no longer holds the consumed token, and NO transient conflict is raised

#### Scenario: Deadline-cut persist adopts a genuine peer rotation on the final re-read

- **GIVEN** a claim winner completed the upstream exchange and the claim/caller deadline has already elapsed
- **AND** a genuinely different peer rotation lands right before the final guarded persist
- **WHEN** the final ciphertext-guarded persist misses the peer's ciphertext and the persist re-reads the row
- **THEN** the stored plaintext is genuinely different, so the peer rotation is ADOPTED (the winner's freshly rotated token is legitimately superseded) rather than overwritten, and no unconditional write is ever issued

#### Scenario: Warmup refresh-claim contention surfaces upstream_unavailable, not invalid_api_key

- **GIVEN** two replicas warm the same account concurrently and a peer replica holds the account's refresh claim
- **WHEN** the warmup submit path's `_ensure_fresh_with_budget` raises a transient `refresh_claim_timeout` (`is_refresh_claim_contention`)
- **THEN** the warmup result and request log record a retryable `upstream_unavailable` error code
- **AND** the healthy account is NOT reported as an `invalid_api_key` authentication failure

#### Scenario: Pinned compact preflight transport-error / permanent failure settles the reservation before raising

- **GIVEN** a file/previous-response-pinned compact-responses request over the HTTP bridge / forwarded path with an `api_key_reservation_override` and `owns_reservation` false
- **AND** its freshness-check preflight fails with either a genuine OAuth `transport_error` `RefreshError` or a permanent `RefreshError`
- **WHEN** the preflight exception handler reaches its terminal raise
- **THEN** the compact API-key usage reservation is settled (released via `_settle_compact_api_key_usage`) before the error is raised, so it does not leak held API-key quota

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

When the bounded status-downgrade compare-and-set is EXHAUSTED without ever
landing — a sustained same-plaintext re-encryption storm the system cannot win
an atomic compare-and-set window against, with no genuinely different peer
rotation ever observed — the system MUST NOT return the success/no-op sentinel
that re-raises the original permanent error, and MUST NOT fall back to an
unconditional (unguarded) status write. Because the system could not
authoritatively persist `reauth_required` under the ciphertext guard, re-raising
the permanent error would send proxy callers into the permanent-failure path (for
example `LoadBalancer.mark_permanent_failure()`), whose status write is NOT
guarded by this refresh-token compare-and-set — so in the storm, or if a genuine
peer re-authentication/import rotation lands after the final re-read but before
that unguarded write, it would clobber a repaired account with `reauth_required`,
the exact clobber the compare-and-set guards prevent. The system MUST instead
raise a transient (non-permanent, transport-level) refresh error that is not
recorded in the permanent-failure cooldown, so the caller retries the whole
refresh once the contention clears rather than running the unguarded permanent
mark. This transient escalation applies ONLY to contention-driven exhaustion
while the account still holds the failed material; a status compare-and-set that
SUCCEEDS still stands as a real permanent failure, and a genuinely different peer
rotation observed on re-read is still adopted as a repair.

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

#### Scenario: Status CAS exhausts on a same-plaintext re-encryption storm

- **GIVEN** this replica's exchange failed permanently and the account still
  holds the same refresh-token plaintext that failed
- **AND** a sustained concurrent re-encryption of that SAME plaintext keeps
  shifting the observed ciphertext, so every conditional status compare-and-set
  misses through the bounded retry budget with no genuinely different peer
  rotation ever observed
- **WHEN** the bounded status-downgrade compare-and-set is exhausted without ever
  landing
- **THEN** the guard raises a transient, non-permanent (transport-level) refresh
  error that is not recorded in the permanent-failure cooldown
- **AND** it does not write `reauth_required` and does not fall back to an
  unconditional status write
- **AND** the original permanent error is not re-raised, so the caller retries
  the whole refresh rather than running the unguarded
  `LoadBalancer.mark_permanent_failure()` path that could clobber a concurrent
  peer rotation

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
