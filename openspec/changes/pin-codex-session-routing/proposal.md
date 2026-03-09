## Why

Codex CLI sends a stable thread `session_id` header on backend Responses and compact requests. `codex-lb` currently ignores that header for routing unless the dashboard-level sticky thread setting is enabled, which allows a compact request to hop to a different upstream account than the one already serving the thread.

That breaks Codex remote compaction semantics for multi-account setups because thread-scoped history can contain upstream-owned encrypted items that must stay on the same account path. The compact retry path also has to keep the provider account header aligned with the refreshed account record, otherwise a correctly pinned thread can still fail upstream after token refresh.

## What Changes

- Treat inbound Codex `session_id` as a routing affinity key for backend Responses and compact requests.
- Preserve that affinity even when `sticky_threads_enabled` is disabled, while keeping existing prompt-cache stickiness for other clients unchanged.
- Ensure compact retries derive the upstream `chatgpt-account-id` header from the refreshed target account instead of the pre-refresh snapshot.
- Add regression coverage for the response-to-compact handoff.

## Impact

- Code: `app/modules/proxy/service.py`
- Tests: `tests/integration/test_proxy_sticky_sessions.py`
- Specs: `openspec/specs/responses-api-compat/spec.md`
