## 1. Sweep entry point

- [x] 1.1 Add `prune_idle_http_bridge_sessions()` to the HTTP bridge mixin: take the bridge lock, reuse `_prune_http_bridge_sessions_locked`, and schedule closes via `_schedule_http_bridge_session_closes` with reason `idle_sweep`

## 2. Heartbeat wiring

- [x] 2.1 Call it from the ring heartbeat loop in `app/main.py`, next to the durable-ownership reconcile, guarded so a failure only logs

## 3. Tests

- [x] 3.1 Idle session is evicted with no request traffic; a freshly-used session is spared
- [x] 3.2 A session with pending work is spared even past its idle TTL
- [x] 3.3 Empty registry is a no-op and schedules no cleanup task

## 4. Spec

- [x] 4.1 Record that idle eviction does not depend on request traffic reaching the replica
