## 1. Dependency

- [x] 1.1 Add `aiohttp-socks>=0.10.1` to the `dependencies` list in `pyproject.toml`.

## 2. SOCKS URL detection

- [x] 2.1 Add `_socks_proxy_url() -> str | None` to `app/core/clients/http.py`.
  - Probe env vars in order: `SOCKS_PROXY`, `socks_proxy`, `ALL_PROXY`, `HTTPS_PROXY`,
    `HTTP_PROXY`, `all_proxy`, `https_proxy`, `http_proxy`.
  - Strip whitespace from each value before inspecting.
  - Skip `HTTP_PROXY`/`http_proxy` when `REQUEST_METHOD` is set (httpoxy guard).
  - Normalise a bare `http://` scheme in `SOCKS_PROXY`/`socks_proxy` to `socks5://`.
  - Normalise `socks5h://` and `socks4a://` to `socks5://` and `socks4://`
    before passing the URL to `ProxyConnector`.
  - Return the first value whose lowercased form starts with `socks5://`,
    `socks5h://`, `socks4://`, or `socks4a://`; return `None` if none match.

## 3. HTTP client construction

- [x] 3.1 In `_build_http_client()`, call `_socks_proxy_url()` once and store the result.
- [x] 3.2 When a SOCKS URL is present, build a `ProxyConnector.from_url(socks_url, ...)`
  with the same `limit`, `limit_per_host`, and SSL context as the plain connector.
  Otherwise build `aiohttp.TCPConnector` as before.
- [x] 3.3 Pass `trust_env=not socks_url` to `aiohttp.ClientSession` for the HTTP session.
- [x] 3.4 For the WebSocket session:
  - When `socks_url` is set **and** `upstream_websocket_trust_env=True`, build a second
    `ProxyConnector.from_url(socks_url, ssl=...)` and set `trust_env=False`.
  - Otherwise build `aiohttp.TCPConnector(ssl=...)` and pass
    `trust_env=settings.upstream_websocket_trust_env` unchanged.
- [x] 3.5 Wrap WebSocket connector + session construction in a `try/except` that closes
  the connector on failure, preventing a connector leak if session construction raises.

## 4. Tests

- [x] 4.1 `tests/unit/test_http_client.py`:
  - `test_socks_proxy_url_detects_lowercase_socks_proxy` — `socks_proxy` env var returns
    the value unchanged when it already has a valid SOCKS scheme.
  - `test_socks_proxy_url_strips_whitespace_from_env_value` — leading/trailing spaces
    are stripped before the scheme check and from the returned URL.
  - `test_socks_proxy_url_normalizes_http_scheme_for_socks_proxy_env` — `http://` scheme
    in `socks_proxy` is normalised to `socks5://`.
  - Extended `socks5h://` and `socks4a://` schemes are normalised to connector-supported
    `socks5://` and `socks4://` URLs.
  - `test_init_http_client_uses_proxy_connector_for_socks_url` — when a SOCKS env var is
    active, both sessions use `ProxyConnector` and `trust_env=False`.

## 5. Spec delta

- [x] 5.1 Add SOCKS proxy requirements to the `outbound-http-clients` delta spec at
  `openspec/changes/add-socks5-proxy-support/specs/outbound-http-clients/spec.md`.
- [x] 5.2 Run `uv run pytest tests/unit/test_http_client.py -q` and confirm clean.
- [x] 5.3 Run `uv run ruff check app/core/clients/http.py tests/unit/test_http_client.py`
  and confirm clean.
