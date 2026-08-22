## MODIFIED Requirements

### Requirement: token_expired at the refresh boundary deactivates the account

The system MUST treat OAuth refresh credential-token or session errors as permanent refresh-token/session failures. Codes include `token_expired`, `app_session_terminated`, `invalid_grant`, `refresh_token_expired`, `refresh_token_reused`, and `refresh_token_invalidated`. The affected account MUST be marked `reauth_required`, MUST remain request-routable with its stored access token, and MUST NOT proactively exchange the known-bad refresh token. A separate upstream account-deactivation signal MUST continue to mark the account `deactivated` and remove it from routing.

Before persisting a permanent refresh failure, the system MUST re-read the account's token material from the database with a real SELECT that bypasses session identity caches, MUST NOT downgrade the account when the refresh token rotated after the failed attempt began (returning the rotated tokens instead), and MUST apply the status downgrade with a compare-and-set conditioned on the freshly observed account state including the refresh-token ciphertext, so a concurrent re-authentication or rotation — even one that leaves status/reason/reset untouched — is never overwritten.

When that status compare-and-set misses, a ciphertext change MUST NOT by itself be treated as a rotation to defer to: because token ciphertext is non-deterministic, a concurrent re-authentication or import can re-encrypt the SAME refresh-token plaintext to different bytes between the fresh re-read and the write. The system MUST compare the freshly observed refresh-token material against the material this attempt exchanged by decrypted-plaintext fingerprint. When the fingerprint is genuinely different the system MUST adopt the stored row without downgrading, and MUST return those rotated tokens to the caller (rather than returning the success/no-op sentinel that lets the caller re-raise the original permanent error) — whether the genuine difference is observed at the initial fresh re-read or only after a status compare-and-set miss. Re-raising in the compare-and-set-miss window would send proxy callers into the permanent-failure path, whose status write must not clobber a peer's valid rotation with stale `reauth_required` state. When the fingerprint is unchanged — the account is still holding the very material that just failed permanently — the system MUST re-read and retry the compare-and-set against the freshly observed ciphertext (bounded) so the downgrade lands, rather than leaving the account active with known-bad refresh credentials.

When the bounded status-downgrade compare-and-set is EXHAUSTED without ever landing — a sustained same-plaintext re-encryption storm the system cannot win an atomic compare-and-set window against, with no genuinely different peer rotation ever observed — the system MUST NOT return the success/no-op sentinel that re-raises the original permanent error, and MUST NOT fall back to an unconditional (unguarded) status write. Because the system could not authoritatively persist `reauth_required` under the ciphertext guard, re-raising the permanent error would send proxy callers into a permanent-failure path that could clobber a concurrent repair. The system MUST instead raise a transient (non-permanent, transport-level) refresh error that is not recorded in the permanent-failure cooldown, so the caller retries the whole refresh once contention clears. This transient escalation applies ONLY to contention-driven exhaustion while the account still holds the failed material; a status compare-and-set that SUCCEEDS still stands as a real permanent refresh failure, and a genuinely different peer rotation observed on re-read is still adopted as a repair.

#### Scenario: Refresh-time `app_session_terminated` is classified as permanent

- **WHEN** `app_session_terminated` is returned by refresh-token exchange
- **THEN** it is classified as a permanent refresh-token/session failure

#### Scenario: Refresh-time `app_session_terminated` requires re-authentication

- **WHEN** refresh-token exchange receives a permanent `app_session_terminated` error
- **THEN** the account is transitioned to `REAUTH_REQUIRED`
- **AND** the reason references the re-login requirement so the dashboard can surface it
- **AND** later ordinary requests may still select the account with its stored access token
- **AND** proactive refresh cycles do not exchange its known-bad refresh token

#### Scenario: Concurrent rotation loser receives refresh_token_reused

- **GIVEN** another replica rotated the account's refresh token and committed while this replica's exchange with the old token was in flight
- **WHEN** this replica's exchange fails with `refresh_token_reused`
- **THEN** no `reauth_required` write occurs
- **AND** this replica returns the rotated tokens from the database

#### Scenario: Status CAS misses on a re-encryption of the same failing token

- **GIVEN** this replica's exchange failed permanently and the account still holds the same refresh-token plaintext that failed
- **AND** a concurrent reauthentication or import re-encrypted that SAME plaintext to different ciphertext between the fresh re-read and the status CAS
- **WHEN** the guard re-reads and finds the refresh-token fingerprint unchanged
- **THEN** it retries the status CAS against the freshly observed ciphertext and lands the `reauth_required` downgrade
- **AND** it does not leave the account active with known-bad refresh credentials

#### Scenario: Peer rotation lands in the status-CAS-miss window

- **GIVEN** this replica's exchange failed permanently and the fresh re-read still showed the same failing refresh-token material
- **AND** a concurrent reauthentication or rotation committed a genuinely different refresh token before the status CAS
- **WHEN** the guard re-reads and finds the refresh-token fingerprint genuinely different from the material this attempt exchanged
- **THEN** it adopts the stored row and returns the peer's rotated tokens
- **AND** no `reauth_required` write occurs and the original permanent error is not re-raised

#### Scenario: Status CAS exhausts on a same-plaintext re-encryption storm

- **GIVEN** this replica's exchange failed permanently and the account still holds the same refresh-token plaintext that failed
- **AND** sustained same-plaintext re-encryption makes every guarded status write miss without a genuine peer rotation
- **WHEN** the bounded status-downgrade compare-and-set is exhausted
- **THEN** the guard raises a transient, non-permanent refresh error outside the permanent-failure cooldown
- **AND** it does not write `reauth_required` or fall back to an unconditional status write

### Requirement: Refresh-path sibling writes never clobber a peer rotation, and the warmup path honors the claim-contention taxonomy

The no-unconditional-write and no-clobber guarantees MUST hold across the WHOLE write surface reachable on the refresh/`ensure_fresh` hot path, not only inside individual refresh helpers. This invariant MUST be enforced structurally at the repository layer so no current or future caller can reopen the clobber class.

Refresh-token ciphertext writes MUST be compare-and-set at the repository layer. The accounts repository MUST expose exactly one method that writes access, refresh, or ID-token ciphertext, and that method MUST take a required `expected_refresh_token_encrypted` predicate. Metadata writes MUST NOT touch token material and MUST use a separate metadata-only operation for identity, plan, workspace, seat, and refresh-timestamp fields. Every token-rotation caller MUST use the guarded writer, and metadata-only callers MUST use the metadata-only writer.

The `chatgpt_account_id` backfill, including the no-refresh fast path, MUST persist through the metadata-only writer. No `ensure_fresh` path may perform an unconditional token write, and no metadata-only path may write token ciphertext.

The post-exchange token persist MUST NOT drop a freshly rotated token on a compare-and-set miss. After upstream exchange consumes the old single-use token and issues a new one, a guarded miss — whether retries are exhausted or the claim/caller deadline cuts the retry loop — MUST run a dedicated, small, bounded final-persist retry loop separate from that deadline. Each attempt MUST remain compare-and-set guarded and MUST re-read on a miss: a genuinely different stored plaintext MUST be adopted, the same plaintext re-encrypted MUST be retried against the new ciphertext, and undecryptable material MUST stop the dedicated retries. Only if all dedicated retries are exhausted while the stored material remains the consumed token MAY the system reach the safe terminal outcome by flagging `REAUTH_REQUIRED` through the same ciphertext-guarded status compare-and-set. No path may fall back to an unconditional token or status write.

The permanent-status downgrade MUST have a single guarded authority. The primary refresh path owns the refresh-token-ciphertext-guarded compare-and-set. A proxy fallback status write MUST also be conditioned on the account's refresh-token ciphertext so a concurrent peer repair causes a miss instead of clobbering the repaired row. A landed `REAUTH_REQUIRED` downgrade MUST remain request-routable and MUST NOT add a process-local routing-unavailable overlay. A landed `DEACTIVATED` downgrade MUST add the local routing-unavailable overlay. A compare-and-set miss caused by a peer repair MUST leave the repaired account selectable.

The proxy warmup submit path MUST classify refresh failures with the same taxonomy as core proxy request paths: transient cross-replica refresh contention MUST surface as retryable `upstream_unavailable`, not `invalid_api_key`; permanent and genuine non-contention transport refresh failures retain their existing classifications.

#### Scenario: Legacy chatgpt_account_id backfill routes through the metadata-only writer

- **GIVEN** a legacy account lacks `chatgpt_account_id` but its stored ID token yields one
- **WHEN** the no-refresh fast path persists the derived identifier
- **THEN** the metadata-only writer does not read or write refresh-token ciphertext
- **AND** a concurrent peer token rotation is untouched

#### Scenario: Repository refuses an unguarded refresh-token write

- **GIVEN** the accounts repository token-writing operation
- **WHEN** any caller persists token ciphertext
- **THEN** a non-optional expected refresh-token ciphertext is required
- **AND** a concurrent rotation turns the stale write into a guarded miss

#### Scenario: Metadata write cannot touch token material

- **GIVEN** a metadata-only identity, plan, or workspace sync
- **WHEN** it writes from a stale account snapshot
- **THEN** token ciphertext remains unchanged

#### Scenario: Proxy permanent-failure mark does not clobber a peer's rotated repair

- **GIVEN** a proxy caller holds stale token ciphertext that failed permanently
- **AND** a peer has already repaired the account with a genuinely rotated token
- **WHEN** the proxy applies its guarded permanent-failure mark
- **THEN** the status compare-and-set misses and performs no write
- **AND** the peer's repaired account remains selectable

#### Scenario: Proxy permanent-failure mark still downgrades when no peer rotation occurred

- **GIVEN** a genuine permanent refresh failure with no concurrent peer rotation
- **WHEN** the guarded status compare-and-set lands as `reauth_required`
- **THEN** no process-local routing-unavailable overlay is added
- **AND** later requests may use the stored access token

#### Scenario: Deactivation downgrade becomes routing-unavailable

- **GIVEN** a permanent failure that maps to `deactivated`
- **WHEN** the guarded status compare-and-set lands
- **THEN** the account is added to the process-local routing-unavailable overlay
- **AND** later requests do not select it

#### Scenario: Post-exchange persist runs dedicated final retries when the deadline cuts the retry loop

- **GIVEN** a claim winner completed upstream exchange and the guarded persist keeps missing after its claim or caller deadline
- **WHEN** the deadline cuts the ordinary retry loop
- **THEN** dedicated bounded final-persist retries still run with ciphertext guards
- **AND** only exhaustion on unchanged consumed material reaches guarded `REAUTH_REQUIRED`
- **AND** no unconditional write occurs

#### Scenario: Deadline-cut persist lands the rotated token when the stored plaintext is unchanged

- **GIVEN** a claim winner completed upstream exchange and the deadline elapsed
- **AND** the stored token plaintext remains the consumed token under changed ciphertext
- **WHEN** final guarded persistence runs against the latest ciphertext
- **THEN** the new rotated token is persisted without a transient conflict

#### Scenario: Deadline-cut persist adopts a genuine peer rotation on the final re-read

- **GIVEN** a genuinely different peer rotation lands before final guarded persistence
- **WHEN** the guarded persist misses and re-reads the row
- **THEN** the peer rotation is adopted and never overwritten

#### Scenario: Warmup refresh-claim contention surfaces upstream_unavailable, not invalid_api_key

- **GIVEN** a peer replica holds an account's refresh claim during warmup
- **WHEN** warmup receives a transient refresh-contention failure
- **THEN** the result and request log record `upstream_unavailable`
- **AND** the account is not reported as `invalid_api_key`

#### Scenario: Pinned compact preflight transport-error / permanent failure settles the reservation before raising

- **GIVEN** a pinned compact request owns an API-key reservation override
- **AND** refresh preflight fails with a genuine transport or permanent refresh error
- **WHEN** the preflight reaches its terminal error
- **THEN** the reservation is settled before the error is raised

## ADDED Requirements

### Requirement: Claimless forced refresh reconciles fresh account state before exchange

When refresh coordination is unavailable or intentionally omitted, a forced refresh MUST freshly re-read the account before upstream exchange. If the stored refresh-token fingerprint changed, the caller MUST adopt the peer's row without exchange. If the fingerprint is unchanged but the fresh status is `REAUTH_REQUIRED` or `DEACTIVATED`, the caller MUST fail permanently without exchanging the terminal refresh material. If the fingerprint is unchanged and status is non-terminal, exchange and persistence MUST use the freshly observed ciphertext as the compare-and-set guard.

#### Scenario: Same token re-encryption is adopted before exchange

- **GIVEN** the fresh row contains the same refresh-token plaintext under different ciphertext
- **WHEN** claimless refresh preflight runs
- **THEN** the fresh ciphertext is adopted before upstream exchange
- **AND** successful rotation is persisted in one guarded write against that ciphertext

#### Scenario: Genuine peer rotation is adopted without exchange

- **GIVEN** the fresh row contains a genuinely different refresh-token fingerprint
- **WHEN** claimless refresh preflight runs
- **THEN** the peer row is adopted
- **AND** no upstream token exchange or persistence write occurs

#### Scenario: Unchanged terminal material fails closed

- **GIVEN** the fresh row remains `REAUTH_REQUIRED` with the same refresh-token fingerprint
- **WHEN** a forced refresh is requested
- **THEN** the refresh fails permanently without exchanging the token
