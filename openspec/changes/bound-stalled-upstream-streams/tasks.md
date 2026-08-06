# Tasks

## 1. Transport keepalive

- [x] 1.1 Enable `SO_KEEPALIVE` on upstream sockets via the connector `socket_factory`
      in `app/core/clients/http.py`, wired into the direct, SOCKS, and websocket
      connectors
- [x] 1.2 Best-effort probe tuning (`TCP_KEEPIDLE` / `TCP_KEEPALIVE`, `TCP_KEEPINTVL`,
      `TCP_KEEPCNT`) guarded so platforms without a knob still construct a client

## 2. Pre-first-byte bound

- [x] 2.1 Carry the effective stream idle timeout into `sock_read` for both streaming
      request paths in `app/core/clients/proxy.py`, replacing `sock_read=None`
- [x] 2.2 Map `aiohttp.SocketTimeoutError` to `StreamIdleTimeoutError` at the stream
      boundary so the existing `stream_idle_timeout` reporting, retry, and failover
      paths apply unchanged. The generic `ClientError` handler runs first, so the
      mapping has to happen before it, and the idle-vs-budget tie-break stays as it was.

## 3. Unroutable event observability

- [x] 3.1 Log unattributed upstream bridge events with event type, response-id presence,
      and pending count; no raw ids or payload content
- [x] 3.2 Stay silent when no pending request is waiting, so drain and retirement paths
      do not produce noise

## 4. Verification

- [x] 4.1 Regression tests: keepalive on constructed sockets, tolerated unsupported
      probe options, pre-header silence reported as `stream_idle_timeout` with
      `sock_read` set to the idle budget, and the unroutable-event log
- [x] 4.2 `uv run pytest tests/unit/test_http_client.py tests/unit/test_proxy_utils.py`,
      `uv run ruff check`, `uv run ruff format --check`
- [ ] 4.3 `openspec validate bound-stalled-upstream-streams --strict` — the OpenSpec CLI
      was not available in the authoring environment; the deltas follow the repository's
      existing `## ADDED/MODIFIED Requirements` + `### Requirement:` + `#### Scenario:`
      structure and still need a CLI run before merge
