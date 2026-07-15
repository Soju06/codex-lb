# Context: serialize-cross-replica-token-refresh

## The race mechanism

1. Replicas A and B each hold the same decrypted refresh token RT0 for one account.
2. An upstream 401 (or the refresh-age threshold, which is shared-clock and fires everywhere at once) triggers `ensure_fresh(force=True)` on both replicas. The process-local singleflight dedupes only within one process.
3. A's `POST /oauth/token` succeeds and rotates RT0 → RT1, but its encrypt+commit has not landed yet.
4. B's exchange fails fast (~200ms) with `refresh_token_reused` (permanent). B's stale-material guard re-reads the account, but `get_by_id` is `session.get`: under READ COMMITTED it cannot see A's uncommitted write and in bound-repo paths it can return a stale identity-map object with no DB read at all. B sees RT0 "unchanged".
5. B writes unconditional `update_status(REAUTH_REQUIRED)`, which also deletes the account's sticky sessions and force-closes all its HTTP-bridge sessions. A's later `update_tokens` commit does not restore status.
6. Result: a healthy account is out of rotation on every replica until a human re-authenticates; warm prompt-cache/bridge affinity is destroyed. Worst case, OpenAI reuse detection revokes the whole token family and RT1 dies too.

## Claim semantics on both backends

The claim is a single conditional upsert (`INSERT .. ON CONFLICT (account_id) DO UPDATE .. WHERE expired-or-mine RETURNING account_id`):

- **PostgreSQL**: concurrent claimers serialize on the conflict row lock; exactly one WHERE passes. Autocommit single statement — no transaction spans the upstream exchange.
- **SQLite**: the statement is atomic under the database-level single-writer lock and safe across processes sharing one file (busy_timeout + a short `database is locked` retry). In-process writers are additionally serialized by `sqlite_writer_section`.

The claim row carries `claim_expires_at` (TTL default 30s, validated >= the refresh-admission wait timeout + 2x the refresh HTTP timeout, since the claim is held across the admission wait and the exchange): a crashed claimant delays other replicas by at most the TTL, after which the conditional upsert succeeds for the next claimer.

## Failure modes

- **Winner exceeds the TTL mid-exchange** (~4x headroom over the 8s HTTP timeout): a second replica may claim and refresh concurrently; the `update_tokens` ciphertext CAS prevents the late writer from clobbering the newer rotation, and a reuse error in that residual window hits the hardened fresh-read guard instead of writing REAUTH.
- **Clock skew**: claim expiry compares the claimant-written `claim_expires_at` against the reader's clock (same caveat as `scheduler_leader`). With a 30s TTL the worst case is a delayed takeover or a brief premature takeover; both are covered by the CAS + post-claim re-read. A DB-clock-domain comparison is a possible follow-up shared with leader-election hardening.
- **DB outage during claim**: the refresh fails transiently (no REAUTH write) — strictly safer than the pre-change behavior, and the refresh's own `update_tokens` would have failed anyway.
- **Loser wait latency**: up to `token_refresh_claim_wait_seconds` added to 401 recovery on the losing replica, bounded by the existing request budget and typically resolved in well under a second by adoption.
- **Guardian dynamic detection misses deployments that never register bridge ring members** — acceptable: the DB claim is the correctness guarantee; the guardian check only trims duplicate upstream load.

## Example

Two replicas, leader election disabled, Helm chart (empty static ring). An access token is invalidated upstream; requests on both replicas 401 simultaneously. Post-change: replica A wins the claim, exchanges RT0→RT1, commits, releases; replica B fails acquisition, polls at 250ms, observes the rotated fingerprint, and returns RT1 from the DB with zero upstream calls. One upstream exchange total; the account stays `active`; sticky and bridge sessions are untouched.

## Operational notes

- `claimed_by` is `http_responses_session_bridge_instance_id` plus a per-process suffix; an over-long instance id is truncated on the instance-id portion only (the suffix is always preserved so co-located workers never collapse into one claimant). Stale rows are self-healing via TTL and are deleted on release, so the table stays at most one row per concurrently refreshing account.
- Landing-order coordination: this change's migration (`20260713_040000_add_account_refresh_claims`) is parented on the current main head `20260712_020000_add_api_key_usage_rollups`. If another in-flight change lands a migration on the same parent first, add an Alembic merge revision (or re-parent) before release so CI sees a single head.
- This change MODIFIES the `usage-refresh-policy` requirement "Multi-replica leader guard", which the harden-scheduler-leader-election change may also touch — coordinate spec-sync merge order.
