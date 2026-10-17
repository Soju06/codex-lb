# Fix unbounded DB wait under the bridge session pending_lock

## Why

In the 2026-08-30 incident (#1968), the livelock's fuel was a wedge: 12+
`_cleanup_http_bridge_submit_interruption` tasks sat blocked on
`session.pending_lock` for up to 4 days. The full audit (#1971) found exactly
two `pending_lock` critical sections that suspend — both submit-admission
sites awaiting the stream-lease reacquire — and inside it the keyed
fair-share threshold resolution awaits the settings cache. The cache refresh
runs a DB query behind a process-global lock, and the asyncpg engine sets no
per-statement timeout, so one query stalled on a half-dead connection wedges
the global settings lock forever and every keyed bridge submit process-wide
then parks while holding its own session's `pending_lock`. #1969 removed the
livelock amplification; this change removes the wedge.

## What Changes

- Resolve the keyed fair-share threshold snapshot BEFORE acquiring
  `session.pending_lock` at both submit-admission sites, and pass it into
  `_ensure_http_bridge_session_stream_lease_locked`; the reacquire under the
  lock no longer performs any settings-cache or DB await. The inline
  resolution remains only as a fallback for lock-free callers.
- Bound every PostgreSQL statement with a fixed asyncpg `command_timeout`
  (60s application constant, like the existing pool timeout/recycle
  constants) so a query stalled on a dead connection surfaces as an error
  instead of an unbounded await under any application lock. Alembic
  migrations use their own synchronous engine and are unaffected.
- Add regression coverage: the reacquire with a provided snapshot never
  touches the settings cache; a stalled settings refresh stalls the submit
  BEFORE `pending_lock` (sabotage-verified against the old in-lock resolve);
  engine connect-args carry the statement bound.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: bridge-session `pending_lock` critical sections
  must not suspend on settings or database reads.
- `database-backends`: PostgreSQL statements are bounded by a fixed
  `command_timeout` alongside the existing pre-ping/recycle hygiene.

## Impact

- A stalled settings-cache refresh now stalls only the requests actually
  waiting on settings — never the per-session `pending_lock`, so interruption
  cleanup and queue bookkeeping keep flowing.
- Any PostgreSQL statement hung on a half-dead connection fails within the
  fixed bound and is retried/surfaced through existing error paths instead of
  wedging its caller (and whatever locks the caller holds) indefinitely.
- Fair-share admission semantics are unchanged: same threshold source, same
  denial envelope; the snapshot is read moments earlier (within the settings
  cache's 5s TTL freshness window). No new settings.
