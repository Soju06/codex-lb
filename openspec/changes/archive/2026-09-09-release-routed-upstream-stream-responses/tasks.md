# Tasks

## 1. Release the routed response

- [x] 1.1 Add the duck-typed `release_codex_response` helper in
      `app/core/clients/codex.py` (`release()` -> `close()` -> `aclose()`, awaited
      only when the result is awaitable)
- [x] 1.2 Release the raw response in the routed branch of `_stream_via_http_attempt`
      before the owned `CodexClient` is closed, on every exit path
- [x] 1.3 Forward `release()` from `_CodexSSEResponse` to the wrapped response
- [x] 1.4 SOCKS: `_SessionOwnedResponse.release()` releases the wrapped response then
      closes the private session; `_SessionOwnedContent` and `read()` reuse it

## 2. Deterministic generator teardown

- [x] 2.1 Consume `_iter_sse_events` under `contextlib.aclosing` on both HTTP
      streaming sites
- [x] 2.2 Propagate `aclosing` through `stream_responses` ->
      `_stream_responses_with_session` -> `_stream_via_http` ->
      `_stream_via_http_attempt` (and the websocket-rejection HTTP fallback) so a
      consumer's `aclose()` reaches the release synchronously

## 3. Verification

- [x] 3.1 Unit: release ordering (release before client close) for terminal break,
      downstream `aclose()`, cancellation, idle timeout, non-2xx error, an
      aclose-only (native egress shaped) response, and a response without either
- [x] 3.2 Unit: SOCKS session-owned response releases before closing its session
- [x] 3.3 Integration: real aiohttp client against an aiohttp.web HTTP proxy that holds
      the tunnel open; zero `Unclosed connection` loop exception-handler events after
      `gc.collect()`, `response.closed`, `response.connection is None`, forwarded
      bytes identical to the upstream frames; repeated for the idle-timeout exit
- [x] 3.4 `pytest tests/unit/test_codex_upstream_paths.py tests/unit/test_codex_client.py
      tests/unit/test_proxy_utils.py tests/integration/test_routed_stream_release.py`,
      `ruff check`, `ruff format --check`, `ty check`,
      `scripts/check_proxy_architecture.py`, `openspec validate --strict`
