## Live Findings

On 2026-05-11, `codex-lb-direct` was healthy on `2455`: `/health/ready` and
`/backend-api/codex/health` returned HTTP 200, Docker reported no restart and
no OOM, and the final 15-minute recurrence probe had zero `database is locked`
log hits. That same probe still showed upstream-level noise: request-log rows
for `upstream_unavailable`, `stream_incomplete`, `context_length_exceeded`,
and `upstream_error`, plus recent Docker log hits for `TimeoutError` and
`Unexpected error during model fetch`.

The earlier hiccup was not a full outage. It was a SQLite write-lock burst from
roughly 2026-05-11 17:37:44 UTC through 2026-05-11 17:42:57 UTC. Logs showed
`sqlite3.OperationalError: database is locked`, ASGI exceptions, request-log
persistence failures, API-key reservation settlement failures, ring heartbeat
failures, sticky-session cleanup failures, and HTTP bridge session release
failures.

Runtime readback showed:

- database URL: `sqlite+aiosqlite:////var/lib/codex-lb/store.db`
- Uvicorn workers: one direct container process
- database pool size: 15
- database max overflow: 10
- SQLite journal mode: WAL
- SQLite synchronous: NORMAL
- SQLite busy timeout: 5000 ms on app connections

The practical operating posture is to keep the live container running when it
is healthy, harden write-hot code paths in source, and use
`scripts/codex_lb_live_snapshot.py` for cheap recurrence checks.
