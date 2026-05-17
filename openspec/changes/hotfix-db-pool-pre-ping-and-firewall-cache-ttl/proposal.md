## Why

After tagging 1.18.0 we deployed it on the production host. Within ~17 minutes of
the container restart we observed a steady stream of HTTP 500 responses on
`/v1/chat/completions` (~4.4% of traffic in a 25-minute window) with the
underlying error

```
sqlalchemy.dialects.postgresql.asyncpg.InterfaceError: connection is closed
[SQL: SELECT sticky_sessions.key ... FROM sticky_sessions WHERE ...]
```

This is the same class of pool-health issue documented in
[#672 (SQLAlchemy QueuePool exhaustion)](https://github.com/Soju06/codex-lb/issues/672).
The root cause is two-fold:

1. **`pool_pre_ping` is not configured on the async engines.** When the
   container restarts, the pool fills with fresh connections; some of those
   connections age past the PostgreSQL server-side idle timeout and are
   silently closed by the server. The next checkout from the pool returns a
   stale connection and the first query raises
   `asyncpg.InterfaceError: connection is closed`. There is no recycle
   policy either, so even healthy pools accumulate increasingly-old
   connections over time.

2. **`FirewallIPCache(ttl_seconds=2)` effectively disables caching.** Every
   request to `/v1/*` and `/backend-api/codex/*` therefore opens a DB session
   to re-check the firewall allowlist. Under load this multiplies the pool
   churn that pre-ping alone has to absorb, and is the primary contributor
   to the QueuePool timeout symptom described in #672.

Both problems are independent of any 1.18.0 behaviour change — the only
reason 1.18.0 surfaced them is that recreating the container reset the pool
to a fresh state, exposing the stale-connection race. This proposal lands a
hotfix targeted at 1.18.1.

## What Changes

- Configure `pool_pre_ping=True` on both the main (`engine`) and background
  (`_background_engine`) async engines when the backend is PostgreSQL. This
  causes SQLAlchemy to issue a cheap `SELECT 1` on every checkout so stale
  connections are detected and replaced before the first real query.
- Configure `pool_recycle=1800` seconds (30 minutes) on the same PostgreSQL
  engines so connections that survive pre-ping are still cycled before any
  reasonable upstream `idle_in_transaction_session_timeout` /
  `tcp_keepalives_idle` boundary.
- Raise the default `FirewallIPCache` TTL from `2` seconds to `30` seconds.
  Add a `firewall_ip_cache_ttl_seconds` setting (env
  `CODEX_LB_FIREWALL_IP_CACHE_TTL_SECONDS`) so operators can tune it.
- Leave SQLite (file + in-memory) unchanged: aiosqlite has no equivalent
  server-side disconnect and `pool_pre_ping` would only add latency. Same
  reason `pool_recycle` only applies to PostgreSQL.
- Both `*_NO_OP` semantics: the changes preserve existing behaviour for
  deployments that have already set `pool_pre_ping`/`pool_recycle` via
  `connect_args` workarounds — those operators just see redundant pings,
  which is harmless.

The `SessionLocal()` non-async-with pattern in
`_ensure_bridge_durable_schema_ready` (`app/main.py:410`) is a related
cleanup discussed in #672 but is **out of scope** for this hotfix; the
existing `try/finally` already closes the session and the call path is
startup-only.

## Out of Scope

- Background-pool size knobs — handled separately by
  `configure-background-db-pool`.
- `SessionLocal()` async-with cleanup in `_ensure_bridge_durable_schema_ready`.
- Pool-size / max-overflow tuning. Defaults remain `15`/`10`; the
  `pool_pre_ping` + cache TTL change is expected to remove the dominant
  source of pool churn we observed, after which the existing burst capacity
  is enough.

## Impact

Existing PostgreSQL deployments get free protection against the
stale-connection race. SQLite deployments are unaffected. Firewall behaviour
is unchanged except that allowlist updates take up to 30 seconds (instead of
2 seconds) to be visible to traffic; existing invalidation hooks
(`cache_poller.on_invalidation` in `app/main.py`, dashboard
`POST/DELETE /api/firewall/ips`) still force-clear the cache immediately, so
operator-driven changes remain instantaneous.
