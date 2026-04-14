## Why

Native Codex clients can stream upstream over the Responses WebSocket transport, but `codex-lb` only exposed coarse environment-based control for that path and did not let operators switch the transport from the dashboard. At the same time, two interoperability gaps remained visible during native Codex testing:

- upstream rejects `service_tier: "fast"` even though Codex fast mode expects priority-tier handling
- operators needed a safe way to compare requested versus upstream-effective service tiers during native Codex testing without changing billable request-log semantics

## What Changes

- Add a dashboard routing setting that lets operators choose the upstream streaming transport: `default`, `auto`, `http`, or `websocket`.
- Support native upstream Responses WebSockets while keeping local client compatibility, account pooling, sticky routing, and retries intact.
- Normalize `service_tier: "fast"` to `"priority"` for upstream compatibility while preserving requested-versus-actual tier observability separately from billable request logs.
- Document the operator-facing dashboard control and the experimental Codex-side feature flags separately from `wire_api = "responses"`.

## Impact

- Code: `app/core/clients/proxy.py`, `app/core/openai/requests.py`, `app/modules/proxy/service.py`, `app/modules/settings/*`, `app/db/*`, `frontend/src/features/settings/*`
- Tests: `tests/unit/test_openai_requests.py`, `tests/unit/test_proxy_utils.py`, `tests/integration/test_proxy_responses.py`, `tests/integration/test_settings_api.py`, `tests/integration/test_openai_compat_features.py`, frontend settings tests
- Specs: `openspec/specs/responses-api-compat/spec.md`, `openspec/specs/frontend-architecture/spec.md`
