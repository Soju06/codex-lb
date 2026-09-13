# Release the Keep-Alive Timer on Connection Loss

## Why

After every completed HTTP/1.1 response the server arms a keep-alive timer
that closes the connection once it has been idle for `--timeout-keep-alive`
seconds. Stock uvicorn (0.52.4 and current `master`) cancels that timer in
`connection_lost` only when the peer closed cleanly (`exc is None`). Any
abnormal close — RST / `ECONNRESET`, `ETIMEDOUT`, any socket read error —
leaves the timer armed. The timer wraps a bound method of the protocol, so the
event loop's timer table keeps the whole per-connection graph reachable
(transport wrapper, request/response cycle, the ASGI scope with every request
header) for the full keep-alive window, while uvicorn's connection accounting
already reports the connection gone.

Reverse proxies purge idle server-side connections with RST (HAProxy verified
50/50), so behind one *every* request leaked its protocol for the whole window.
With the default of 7200 s that was ~18k retained protocol graphs per two hours
in production and a 2 GiB memcg OOM on 2026-09-03. A real-socket reproduction
retains 300/300 protocols after an RST close on both asyncio and uvloop, and
0/300 with the fix.

The 7200 s default itself dates from #729, which raised it from 300 s without a
recorded measurement. The race the flag guards against — a client reusing a
pooled idle connection at the moment the server closes it — is prevented by
`server idle window > client pool idle timeout`; it does not scale with request
size. Codex CLI (reqwest) drops pooled connections after 90 s, so 300 s already
leaves a 3.3x margin, while 7200 s multiplies the blast radius of any retention
defect by 24x and holds real idle sockets for two hours.

## What Changes

- The application's HTTP protocol classes (`UpgradeTolerantHttpToolsProtocol`,
  `UpgradeTolerantH11Protocol`) release the keep-alive timer on every
  connection loss, not only on a clean close. Clean-close teardown and the
  timer's idle-close behavior are unchanged; no forwarded bytes change.
- The default idle keep-alive window returns to 300 s (the pre-#729 value and
  what production has run since the incident). `--timeout-keep-alive` /
  `UVICORN_TIMEOUT_KEEP_ALIVE` keep overriding it; the help text now states the
  invariant the value must satisfy.
- Regression coverage at the protocol layer (fake transport, both subclasses),
  over real sockets with the production wiring (RST close), and canaries
  pinning the stock uvicorn behavior so the override can be retired when
  upstream fixes it.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `http-ingress-limits`: adds a requirement that no per-connection keep-alive
  state outlives a lost connection, and a requirement for the bounded,
  configurable idle keep-alive window and its default.

## Impact

`app/core/http_protocol_httptools.py`, `app/core/http_protocol.py`
(`connection_lost` overrides), `app/cli.py` (default and help text),
`tests/integration/test_http_keepalive_timer.py` (new),
`tests/integration/test_http_upgrade_tolerance.py` (helper kwargs, retirement
notes), `tests/unit/test_cli.py` (pinned default), `docs/deployment/remote.md`
(operator note). No API, schema, or dashboard change. Operators who rely on an
idle window above 300 s for a direct-connect client with an unusually long pool
idle timeout set `UVICORN_TIMEOUT_KEEP_ALIVE` explicitly.
