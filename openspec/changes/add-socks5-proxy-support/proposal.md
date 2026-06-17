## Why

Operators deploying codex-lb in network environments that route egress through a
SOCKS proxy (corporate networks, VPNs, containerised egress gateways) have no way to
tunnel the outbound HTTP and WebSocket sessions through that proxy today. `aiohttp`'s
`trust_env=True` setting only honours HTTP/HTTPS proxy env vars natively; SOCKS
support requires `aiohttp-socks` and an explicit `ProxyConnector`.

This change adds automatic SOCKS4/SOCKS4a/SOCKS5/SOCKS5h proxy detection via
standard environment variables so operators can configure egress without code changes.

## What Changes

- Add `aiohttp-socks>=0.10.1` to the project dependencies.
- Add `_socks_proxy_url()` in `app/core/clients/http.py` that inspects the following
  env vars in priority order, returning the first value that starts with a SOCKS
  scheme (`socks5://`, `socks5h://`, `socks4://`, `socks4a://`):
  `SOCKS_PROXY`, `socks_proxy`, `ALL_PROXY`, `HTTPS_PROXY`, `HTTP_PROXY`,
  `all_proxy`, `https_proxy`, `http_proxy`.
  Additional behaviour:
  - Values are stripped of leading/trailing whitespace.
  - A bare `http://` scheme in `SOCKS_PROXY`/`socks_proxy` is normalised to
    `socks5://` (handles misconfigured env vars while staying parseable by the
    configured proxy connector).
  - `socks5h://` and `socks4a://` are normalised to `socks5://` and `socks4://`
    before building the connector because the installed parser rejects the
    extended schemes.
  - `HTTP_PROXY`/`http_proxy` are skipped when `REQUEST_METHOD` is set in the
    environment (CGI/httpoxy security convention).
- Modify `_build_http_client()`:
  - When a SOCKS URL is detected, build a `ProxyConnector` (from `aiohttp-socks`)
    instead of `aiohttp.TCPConnector` for the shared HTTP session.
  - Set `trust_env=False` on both the HTTP and WebSocket sessions when a
    `ProxyConnector` is active, to prevent aiohttp from double-proxying.
  - Route the WebSocket session through the same SOCKS proxy when
    `upstream_websocket_trust_env=True` (operator opt-in); otherwise use the
    plain `TCPConnector` for WebSocket connections as before.
  - Wrap the WebSocket connector/session construction in a `try/except` so the
    first connector is closed if the second construction fails (connector leak fix).
- Cover the new behaviour with unit tests in `tests/unit/test_http_client.py`:
  - `_socks_proxy_url` env var detection (case, whitespace, scheme normalisation).
  - `init_http_client` uses `ProxyConnector` and `trust_env=False` for both
    sessions when a SOCKS env var is active.

## Impact

- Operators can now configure SOCKS4/SOCKS5 egress by setting a single env var
  (e.g. `SOCKS_PROXY=socks5://gateway:1080`). No code or config-file changes
  required beyond the env var.
- When no SOCKS env var is set, the code path is identical to before (no behaviour
  change for existing deployments).
- `trust_env=False` is set when a `ProxyConnector` is active; this prevents
  aiohttp from independently picking up a second HTTP/HTTPS proxy from the
  environment and double-proxying. Operators that previously relied solely on
  `trust_env=True` for HTTP/HTTPS proxies are unaffected (no SOCKS env var → same
  behaviour as before).
- No changes to the proxy request/response contract, account routing, dashboard,
  OAuth flow, or any other operator-visible surface.
