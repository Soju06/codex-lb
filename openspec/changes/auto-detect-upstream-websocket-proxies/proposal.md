## Why

`codex-lb` already honors `http_proxy` / `https_proxy` for most outbound HTTP calls via `aiohttp`
sessions with `trust_env=True`, but the upstream Responses websocket path explicitly disables env
proxy discovery unless operators set `CODEX_LB_UPSTREAM_WEBSOCKET_TRUST_ENV=true`.

That opt-in default makes proxy-enabled deployments look broken: exporting standard proxy variables
isn't enough for the websocket path that Codex-native traffic prefers, so requests bypass the local
proxy and fail in restricted networks. The same path also doesn't surface `all_proxy` values into
`websockets.connect`, which prevents SOCKS fallback from working when operators rely on the usual
shell exports.

## What Changes

- Auto-enable upstream websocket env-proxy usage whenever standard outbound proxy variables are
  present, while keeping `CODEX_LB_UPSTREAM_WEBSOCKET_TRUST_ENV=false` as an explicit direct-connect
  override
- Resolve websocket proxy URLs from the standard env variables with precedence for websocket- and
  scheme-specific settings before falling back to `all_proxy`
- Add the `python-socks` runtime dependency so websocket handshakes can use SOCKS proxies when
  `all_proxy` or `wss_proxy`/`ws_proxy` points to a SOCKS endpoint
- Update regression coverage and operator docs for the new default

## Impact

- Code: `app/core/config/settings.py`, `app/core/clients/proxy_websocket.py`, `app/core/utils/proxy_env.py`, `app/core/clients/http.py`
- Tests: `tests/unit/test_proxy_websocket_client.py`, `tests/unit/test_http_client.py`, `tests/unit/test_settings_multi_replica.py`
- Docs: `README.md`, `.env.example`
- Specs: `openspec/specs/outbound-http-clients/spec.md`
