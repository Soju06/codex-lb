## Why

On a single-instance SQLite deployment, every upstream turn built a fresh
`aiohttp.TCPConnector` whose `ssl.create_default_context()` +
`load_verify_locations(certifi.where())` read the whole CA bundle on the event
loop. Sampling the live process during a 74.8 s event-loop stall put the main
thread inside `SSLContext.set_default_verify_paths` for at least 17 s of it
(issue #2029).

That stall then breaks an unrelated invariant. The bounded SQLite teardown
(issue #1682) measures its 5 s deadline in wall-clock time, so a starved loop
reaches the deadline while the aiosqlite worker has already finished and only
its completion callback is queued. The reclaim then fences a healthy session
and invalidates a live connection, and the interrupt fails with
`ValueError: no active connection` or
`sqlite3.ProgrammingError: Cannot operate on a closed database`. Over 24 h this
instance logged 82 such reclaims, 100% of them inside an `event_loop_lag`
window, with 8 of the 13 on the current build failing that way.

## What Changes

- Build the outbound SSL context once per process (`shared_ssl_context`) and
  reuse it from the shared HTTP/WebSocket connectors, Codex direct sessions,
  Codex SOCKS sessions, and the settings SOCKS probe. `_build_ssl_context`
  stays the uncached constructor.
- After the teardown bound expires, observe the abandoned rollback/close for a
  short shielded grace. When it has *successfully* completed, skip the reclaim
  entirely and report the elapsed bound instead. Failed, cancelled, and still
  pending teardowns keep the existing reclaim unchanged.
- Carry `elapsed_seconds` into both teardown log lines, and raise a failed
  connection invalidation from debug to warning so a real permanent hold
  (issue #1981) stays visible.

## Impact

- Removes a per-turn synchronous CA-bundle read from the event loop; trust-root
  changes on disk now take effect at process restart instead of per connector.
- A healthy session is no longer fenced, and a live connection no longer
  invalidated, because the loop was late.
- No new setting, dependency, migration, or compatibility path. Public
  request/response schemas, routing, and account ownership are untouched.
