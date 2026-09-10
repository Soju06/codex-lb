# Tasks

## 1. Consolidation

- [x] 1.1 Add `_await_result_deferring_cancellation` / `_await_cleanup_deferring_cancellation` wrappers next to the canonical `_await_task_deferring_cancellation` in `app/core/utils/shared_future.py`.
- [x] 1.2 Route the remaining copies through them: websocket helpers (alias import), proxy api and model-sources forwarding (bool-marker adapters), db session `_shielded` (re-raise adapter).
- [x] 1.3 Convert the inline `api_key_usage` shield loops (deferred keyed-health drain, fallback release, ordering-sensitive settle wait) to `wait_on_shared_future`, preserving their site-specific control flow.

## 2. Spec Qualification

- [x] 2.1 Qualify the `database-backends` `command_timeout` requirement: best-effort against a blackholed peer (asyncpg's cancellation handshake talks to the server); lock discipline remains the primary defense.

## 3. Regression Coverage

- [x] 3.1 Identity-equality test pinning the alias imports to the canonical implementation.
- [x] 3.2 Marker test: the api/forwarding bool adapters report a level-cancelled scope after cleanup completes.
- [x] 3.3 Existing defer-leak, idle-leases, shared-future, db-session, forwarding, and websocket suites stay green.

## 4. Verification

- [x] 4.1 ruff format/check + ty on all touched files; strict OpenSpec validation.
