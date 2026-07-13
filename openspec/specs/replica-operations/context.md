# replica-operations Context

## Purpose

codex-lb ships first-class multi-replica machinery — the HTTP bridge instance ring with owner
forwarding, DB-backed leader election, the cache-invalidation bus, DB-backed sticky sessions and
rate limiting — but the prerequisites for actually running more than one replica were scattered
across code comments and the Helm README. This capability is the single normative home for the
topology contract: what an operator must provision, which failure modes are guarded at startup,
and which cross-replica behaviors are best-effort.

## Topology overview

A supported multi-replica deployment looks like:

- **Shared PostgreSQL** (`CODEX_LB_DATABASE_URL=postgresql+asyncpg://...`) — every cross-replica
  coordination primitive lives in this database: `scheduler_leader` (leader lease),
  `bridge_ring_members` (ring membership + advertise endpoints), `http_bridge_sessions`
  (bridge session ownership), `cache_invalidation` (settings/selection cache bus),
  `sticky_sessions`, `rate_limit_attempts`, and `runtime_sentinels` (startup consistency
  sentinels such as the encryption-key fingerprint).
- **Leader election opt-in** — `CODEX_LB_LEADER_ELECTION_ENABLED=true`. Without it every replica
  self-elects and singleton schedulers (usage refresh, automations, retention, quota planner,
  api-key reset, sticky-session cleanup, auth guardian) run N-fold. The auth guardian is the one
  scheduler that self-disables in that configuration, and it logs a startup WARNING when it does.
- **Bridge ring identity** — a unique `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID` per
  replica, the full ring in `..._INSTANCE_RING`, and a reachable replica-specific advertise URL
  so hard-continuity requests landing on the wrong replica can be forwarded to the owner (see
  `sticky-session-operations` and `responses-api-compat` for the forwarding mechanics).
- **Shared encryption key** — the same `encryption.key` file mounted on every replica. Verified
  at startup against the `runtime_sentinels` fingerprint (below).

SQLite remains fully supported for exactly one application process. The leader lease is bypassed
on SQLite (every process self-elects), so `uvicorn --workers N` or two containers sharing one
SQLite file duplicate every singleton scheduler and risk `database is locked` failures — and
corruption on network filesystems. Scaling out means moving to PostgreSQL.

## Why the encryption-key fingerprint sentinel

Divergent encryption keys do not fail at startup; they fail replica-dependently at use time:
dashboard session cookies minted by one replica 401 on another, encrypted proxy credentials and
OAuth tokens fail to decrypt, and bridge owner-forwarding HMAC signatures are rejected with
`bridge_forward_invalid`. The sentinel turns that class of misconfiguration into a deterministic
startup failure. `sha256` over the raw key bytes is exact (the key file *is* the key), and the
insert-if-absent stamp needs no advisory lock: PostgreSQL primary-key uniqueness arbitrates the
concurrent first boot, and SQLite's single-writer lock makes insert-or-noop atomic.

### Runbook: intentional key rotation

1. Stop all replicas.
2. Replace the mounted `encryption.key` on every replica (re-encrypt stored secrets as needed).
3. Delete the stale sentinel: `DELETE FROM runtime_sentinels WHERE name = 'encryption_key_fingerprint';`
4. Start the replicas; the first one stamps the new fingerprint.

Escape hatches: `CODEX_LB_ENCRYPTION_KEY_FINGERPRINT_MODE=warn` (log ERROR, continue) or `off`.
Leave it at `enforce` in production — a warn-mode mismatch means some fraction of logins and
bridge forwards are already failing.

## Settings optimistic locking

`PUT /api/settings` is a full-row read-modify-write over ~40 fields. Before versioning, two
concurrent saves (two dashboard tabs, or two replicas' dashboards) silently lost the first
writer's fields — including security toggles — with the audit log recording only the winner's
diff. `dashboard_settings.version` now rides every ORM update (`WHERE id = 1 AND version = :old`),
so the loser gets `409 settings_conflict` and nothing is partially applied. Clients may send
`expectedVersion` (from a previous GET/PUT response) to also detect staleness that predates the
request; the dashboard frontend does not send it yet (follow-up), so server-side race protection
is what is guaranteed today. Internal single-field writers (password, TOTP, guest password)
retry once on conflict because their mutations are idempotent absolute writes.

## Metrics semantics

- **One replica, multiple workers**: without `PROMETHEUS_MULTIPROC_DIR`, each worker owns a
  private registry and only the worker that wins the metrics-port bind serves `/metrics` — i.e.
  roughly 1/N of traffic. The losing workers now log an ERROR naming the remediation. Set
  `PROMETHEUS_MULTIPROC_DIR` to a writable directory shared by the workers to aggregate.
- **Multiple replicas**: counters are per-replica by design. Scrape every replica individually
  (per-pod scrape targets / ServiceMonitor); scraping through a load-balanced VIP samples a
  random replica per scrape and is unsupported.

## Known limitations (triaged follow-ups)

- **Dashboard OAuth flows require replica affinity** — the PKCE verifier, state token, and
  device-poll handle live in process memory, so `/api/oauth` start/status/complete must land on
  the same replica. Follow-up: `persist-oauth-flow-state`.
- **Websocket turns are not drained on shutdown, and detached request-log writes may be lost at
  teardown** — drain rejects new HTTP work but neither rejects new websocket scopes nor waits
  for in-flight websocket turns, and pending request-log tasks are not flushed before the DB
  closes. Follow-up: `graceful-drain-lifecycle`.
- **`file_id` → account pins are process-local best-effort** — file finalize/input_file requests
  landing on another replica can route to an account that does not own the file. Follow-up:
  `persist-file-account-pins`.
- **Concurrent cross-replica usage refresh can transiently tear `additional_usage_history`** —
  the per-account delete+insert rewrite is non-transactional across refreshers; the tear
  self-heals within one refresh interval. Documented limitation; no follow-up scheduled.
- **Selection/settings caches converge within their TTLs** — cross-replica cache invalidation is
  bus + TTL bounded; see `query-caching` and the in-flight `extend-cache-invalidation-bus`
  change for tightened convergence.
