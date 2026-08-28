# Tasks: raise-ws-ingress-message-limit

## 1. Websocket ingress budget (CLI/env → uvicorn)

- [x] 1.1 Add `--ws-max-size` argument to `app/cli.py` (default `os.getenv("UVICORN_WS_MAX_SIZE", "134217728")`), with a `_parse_server_ws_max_size` validator rejecting non-integer or non-positive values, following the `--timeout-keep-alive` pattern
- [x] 1.2 Pass `ws_max_size=` through to `uvicorn.run(...)` in `main()`
- [x] 1.3 Unit tests in `tests/unit/test_cli.py`: default value, env override, flag override, flag-beats-env, invalid value exits with error, and `uvicorn.run` receives `ws_max_size`

## 2. Fail-fast 400 for oversized response.create

- [x] 2.1 Change `_enforce_response_create_size_limit` in `app/modules/proxy/_service/response_create.py` to raise `ProxyResponseError(400, ...)` instead of `413`
- [x] 2.2 Update unit assertions in `tests/unit/test_proxy_utils.py` (guard status 413 → 400)
- [x] 2.3 Update integration assertions at the product paths: `tests/integration/test_proxy_websocket_responses.py` (websocket error event `status == 400`) and `tests/integration/test_http_responses_bridge.py` (HTTP response status 400)

## 3. Docs & spec sync

- [x] 3.1 Add reverse-proxy sizing/upgrade guidance and the ingress-budget knob to `openspec/specs/responses-api-compat/ops.md`
- [x] 3.2 Record the incident/rationale summary in `openspec/changes/raise-ws-ingress-message-limit/context.md`
- [x] 3.3 Validate specs: `openspec validate --specs` (delta) and strict change validation

## 4. Review follow-up (Codex P2: launcher parity)

- [x] 4.1 `scripts/distroless-entrypoint.py`: exec `python -m app.cli` instead of `fastapi run` so the websocket ingress budget (and keep-alive) apply, matching `scripts/docker-entrypoint.sh`
- [x] 4.2 `docker-compose.yml` (dev): launch via `uvicorn app.main:app --reload --ws-max-size 134217728` instead of `fastapi run` so dev keeps reload while matching the production ingress budget

## 5. Verification

- [x] 4.1 Run targeted test suites: `uv run pytest tests/unit/test_cli.py tests/unit/test_proxy_utils.py tests/integration/test_proxy_websocket_responses.py tests/integration/test_http_responses_bridge.py`
- [x] 4.2 End-to-end check: start the server with default flags, open a websocket to `/backend-api/codex/responses`, send a >16 MiB `response.create`, and confirm the connection survives to an application-level response (slimmed forward or status-400 error event), not a `1009` close
