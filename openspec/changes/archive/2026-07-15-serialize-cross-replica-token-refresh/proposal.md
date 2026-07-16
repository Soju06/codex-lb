# Serialize Cross-Replica Token Refresh

## Why

OpenAI refresh tokens are single-use/rotating (the codebase itself lists `refresh_token_reused` and `invalid_grant` in `PERMANENT_FAILURE_CODES`), but token refresh is deduplicated only by the process-local singleflight in `AuthManager`. When two replicas both hit an upstream 401 (`ensure_fresh(force=True)`) or both cross the refresh-age threshold, both POST the same refresh token; the loser gets a fast permanent 4xx, its stale-material guard reads a pre-commit/identity-map row (`get_by_id` is `session.get`), and it writes an unconditional `update_status(REAUTH_REQUIRED)` — which also deletes sticky sessions and force-closes all bridge sessions — permanently removing a healthy account from rotation until a human re-authenticates. The auth guardian compounds this because its multi-replica guard checks only the static bridge instance ring, which Helm and compose deployments deliberately leave empty, so every replica runs the guardian's concurrent force-refreshes.

## What Changes

- New `account_refresh_claims` table (account_id PK) + Alembic migration: a per-account, TTL-bounded cross-replica refresh claim acquired via a conditional upsert that is atomic on both PostgreSQL (ON CONFLICT row lock) and SQLite (single-writer lock).
- `AuthManager.refresh_account` acquires the claim before any upstream OAuth exchange; after winning it re-reads the account fresh from the DB (`populate_existing`) and skips the upstream call if the refresh-token material already rotated; the claim is released after `update_tokens` commits; no DB lock/transaction is ever held across upstream I/O.
- Claim losers wait with a bounded poll (adopting the winner's rotated tokens from the DB without calling upstream); on wait timeout they raise a transient (non-permanent, transport-style) `RefreshError` so the 401-recovery path fails over to another account instead of blocking or writing `REAUTH_REQUIRED`.
- Permanent-refresh-failure hardening: re-read token material with a fresh SELECT (bypassing the identity map) before persisting, and replace the unconditional `update_status(REAUTH_REQUIRED)` with the existing `update_status_if_current` CAS; `update_tokens` gains an optional expected-refresh-token-ciphertext CAS guard.
- Auth guardian multi-replica detection: keep the static-ring build-time check but add a per-tick dynamic check counting live `bridge_ring_members` heartbeats; with >1 live replicas and leader election disabled, the guardian skips the pass with a warning.
- New settings: `token_refresh_claim_ttl_seconds` (default 30, validated >= 2x `token_refresh_timeout_seconds`), `token_refresh_claim_wait_seconds` (default 8.0), `token_refresh_claim_poll_seconds` (default 0.25).
- Spec deltas in `usage-refresh-policy`; regression tests simulating two replicas as two sessions/AuthManagers on one DB, plus a route-level regression on the proxy responses path.
