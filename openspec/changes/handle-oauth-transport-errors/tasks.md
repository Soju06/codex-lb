## 1. Client-layer translation

- [x] 1.1 Add `_post_auth_request` to `app/core/clients/oauth.py`: translate `aiohttp.ClientError` and `asyncio.TimeoutError` raised by the leased `POST` into `OAuthError("transport_error", <fixed message>)`, logging the exception class only.
- [x] 1.2 Route the authorization-code exchange, device-code request, and device-token poll direct-egress posts through it; leave the routed `_codex_post` path and all status/payload/pending handling unchanged.

## 2. Verification

- [x] 2.1 Prove the defect before the fix: browser callback (aiohttp 500 / raw `ClientConnectorError`), device `start` (500 instead of the dashboard envelope), device-token poll (flow stuck `pending`).
- [x] 2.2 Integration regression tests at the product path with only the outbound HTTP boundary patched: callback error page + terminal `error` status + no code/state in body or logs; `POST /api/oauth/start` `forceMethod=device` non-500 envelope; device-token poll finalizing `error`.
- [x] 2.3 Run `tests/integration/test_oauth_flow.py` and `tests/unit/test_oauth_client.py`, `ruff check app tests`, `ruff format --check app tests`, `openspec validate --specs`, and `openspec validate handle-oauth-transport-errors --strict`.
