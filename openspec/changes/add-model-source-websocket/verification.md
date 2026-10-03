# Upstream port verification — 2026-10-03

Base: current main `f8ffbac2099a113fba54dfd8d77774f5bca80ffa` with the alias and source-ownership prerequisites. Source workspace HEAD `67294897` and its uncommitted changes remain untouched.

## Final local checks

- Native transport, HTTP error contracts, ingress limits, catalog policy, keepalive, lifecycle, quota/authorization and migration suites: **310 passed** (`TMPDIR=/dev/shm uv run pytest -n 2 --dist=loadfile tests/integration/test_model_source_websocket*.py tests/unit/test_model_source_websocket_transport.py -q`).
- Subscription WebSocket, source dispatch/routing, alias, forwarding and replay compatibility: **667 passed** (`tests/integration/test_proxy_websocket_responses.py`, `test_model_source_dispatch.py`, `test_model_source_routing.py`, `test_model_source_aliases.py`, `tests/unit/test_model_sources_forwarding.py`, `test_replay_safety.py`).
- Model-source frontend: **31 passed**; ESLint, TypeScript and production build passed.
- `make lint`, whole-repository Python type checking, SQLite `make migration-check`, strict change validation and all **68** main specs passed.
- Synthetic before/after dashboard screenshots are in `evidence/`.

## Port decisions and corrected test assumptions

The upstream removed subscription-overflow and the fork's key dashboard is absent. Source-only replay helpers reuse unchanged subscription predicates; no overflow runtime or installer endpoints were restored. Catalog tests exercise `/backend-api/codex/models` and account for its existing released request reservation, including enforced models. Native tests no longer patch removed fork settings/caches. Declared namespace tests use upstream's existing `experimental_supported_tools` metadata.

The port includes the HTTP ownership error-envelope correction and bounded delayed-pong keepalive. Intermediate failures identified stale fork fixture assumptions; the final native and compatibility runs above pass on the port.

## Remaining draft gates

Maintainer product/design review, real client handshake/fallback behavior, intended-provider native protocol conformance, PostgreSQL migration/storage tests, and current-head cloud CI/review are outstanding. Docker API access is unavailable to this account; PostgreSQL is not claimed verified. No source capability was enabled and no production deployment or data change occurred. Keep this change active until review and merge/archive gates are satisfied.
