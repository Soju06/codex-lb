## MODIFIED Requirements

### Requirement: Background usage refresh is staggered across accounts

Background usage refresh MUST distribute account refresh attempts across the fixed 60-second usage refresh interval (`USAGE_REFRESH_INTERVAL_SECONDS` in `app/core/usage/refresh_policy.py`, which also derives the 180-second usage freshness horizon) instead of refreshing every eligible account in one burst. Background usage refresh MUST always run; the request-path refreshes (account import, post-`usage_limit_reached` request refresh) MUST NOT be gated by an operator switch, and `CODEX_LB_USAGE_REFRESH_ENABLED` / `CODEX_LB_USAGE_REFRESH_INTERVAL_SECONDS` are removed settings that startup reports and ignores. Each scheduler slice MUST attempt at most one eligible account. Over a full cycle, all eligible accounts SHOULD be considered once.

Each slice MUST select its account before reading usage history and MUST scope its latest-usage lookups, updater input, warm-up candidate evaluation, and recoverable-status evaluation to that selected account. The scheduler MAY retain the full eligible account roster only to choose the deterministic rotation and calculate staggered warm-up phases; that roster MUST NOT cause usage-history reads, upstream refresh attempts, warm-up sends, or status mutations for an unrelated account in the slice. A selected-account refresh failure MUST NOT trigger same-slice fallback to another account. Database sessions used to load scheduler state MUST close before upstream network I/O begins, and concurrent follow-up work MUST NOT share an `AsyncSession`.

#### Scenario: Scheduler refreshes one account per slice

- **GIVEN** two active accounts are eligible for usage refresh
- **WHEN** the scheduler runs consecutive refresh slices
- **THEN** the first slice attempts one account
- **AND** the second slice attempts the other account
- **AND** cache invalidation for usage-derived routing state runs at the cycle boundary

#### Scenario: Unrefreshable accounts are skipped by scheduler rotation

- **GIVEN** one account is active
- **AND** one account is deactivated
- **AND** one account requires re-authentication
- **WHEN** the scheduler builds the refresh rotation
- **THEN** only the active account is considered

#### Scenario: Selected slot scopes usage history and follow-up work

- **GIVEN** two eligible accounts have stored primary, secondary, and monthly usage
- **AND** the first account is selected for the current scheduler slice
- **WHEN** the scheduler reads before/after usage and evaluates warm-up and recoverable status
- **THEN** every usage-history lookup is filtered to the first account
- **AND** only the first account is passed to usage refresh, warm-up candidate evaluation, and recoverable-status evaluation
- **AND** the second account cannot be mutated or contacted during that slice

#### Scenario: Warm-up phase cohort does not widen evaluation scope

- **GIVEN** multiple warm-up-enabled accounts participate in staggered-idle phase calculation
- **AND** one account is selected for the current usage-refresh slice
- **WHEN** refreshed usage is evaluated for warm-up
- **THEN** the phase calculation retains the eligible fleet cohort
- **AND** only the selected account can create a warm-up attempt or send warm-up traffic

#### Scenario: Selected-account failure does not fail over within the slice

- **GIVEN** two accounts are eligible for scheduler rotation
- **AND** the first account is selected
- **WHEN** that account's usage refresh fails
- **THEN** the scheduler does not attempt the second account in the same slice
- **AND** the second account remains eligible for its normal later slice

#### Scenario: Scheduler session closes before selected-account network work

- **GIVEN** the scheduler loaded the account roster and selected account usage
- **WHEN** the selected account's upstream refresh starts
- **THEN** the scheduler read session is already closed
- **AND** any concurrent warm-up follow-up owns an independent database session

### Requirement: Cross-replica token refresh serialization

Before any upstream OAuth token exchange for an account, the system MUST acquire that account's row in `account_refresh_claims` via a conditional upsert that succeeds only when no unexpired claim by another claimant exists; the upsert MUST be atomic on both PostgreSQL (ON CONFLICT row lock) and SQLite (single-writer lock). After acquiring, the system MUST re-read the account's refresh-token material fresh from the database (bypassing session identity caches) and MUST skip the upstream exchange when the material has rotated since the refresh was requested, adopting the stored tokens instead. Claims MUST carry an expiry covering all work performed under the claim (TTL at least the refresh-admission wait timeout plus twice the refresh HTTP timeout, because the claim is held across the admission wait and the OAuth exchange) so a crashed claimant cannot block refresh indefinitely while a healthy claimant cannot lose its claim mid-work, MUST be released after the refreshed tokens are persisted, and MUST NOT be held as an open database transaction or lock across upstream network I/O. The claim expiry — BOTH the stored `claim_expires_at` AND the takeover predicate that treats an existing claim as expired — MUST be evaluated on the DATABASE server clock (`clock_timestamp()`/`now()` on PostgreSQL; in-statement `strftime(..., 'now')` on SQLite), never against a replica-local Python wall-clock instant captured before the statement executes, so inter-replica clock skew can never let one replica treat another replica's still-live claim as expired and steal it (which would let two replicas exchange the same single-use refresh token concurrently). This mirrors the clock-domain guarantee of the scheduler leader election. The claim TTL is not operator-configurable: it is the fixed helper `max(30 s, admission wait + 2 x refresh HTTP timeout)` in `app/modules/accounts/auth_manager.py` (30 s with the fixed 10 s admission wait and 8 s refresh timeout), so it always satisfies the floor by construction, and `CODEX_LB_TOKEN_REFRESH_CLAIM_TTL_SECONDS` is a removed setting that startup reports and ignores. The claimant identity MUST remain unique per OS process even when the configured instance id exceeds the stored column width (truncate the instance-id portion, never the per-process suffix). The per-process suffix MUST be derived per OS process and resolved at claim-build time (for example incorporating `os.getpid()`), never frozen at module import: in pre-fork/multi-worker deployments a module imported before the fork boundary MUST NOT hand every forked child an identical suffix, so two sibling workers sharing one instance id build DISTINCT claimant identities (and thus distinct `claimed_by` values) rather than both satisfying the re-entrant claim upsert and refreshing the single-use token concurrently. The suffix MUST also remain stable across repeated calls within a single process so genuine same-process re-entrant claims still match. The same fork-safety MUST hold for the coordinator that composes claims: a process-default/auto-derived claimant identity MUST NOT be frozen when the coordinator is constructed (the process-default coordinator is commonly built during preload/startup, before a pre-fork server forks its workers, and a frozen identity would be inherited identically by every child). It MUST instead be resolved per OS process at use time so two forked children build DISTINCT claimant identities; a claimant identity that a caller explicitly injects MUST remain stable and unchanged (including across a fork), and repeated reads within one process MUST stay stable.

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

- **GIVEN** a deployment whose environment still sets `CODEX_LB_TOKEN_REFRESH_CLAIM_TTL_SECONDS`, `CODEX_LB_TOKEN_REFRESH_TIMEOUT_SECONDS` or `CODEX_LB_PROXY_ADMISSION_WAIT_TIMEOUT_SECONDS` to any value
- **WHEN** settings are constructed and a replica later acquires a refresh claim
- **THEN** construction succeeds (the values are ignored and startup logs the removed-setting warning once; nothing is rejected)
- **AND** the claim TTL is the fixed 30 s (`max(30 s, 10 s admission wait + 2 x 8 s refresh timeout)`), which covers the admission wait plus twice the refresh timeout

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
