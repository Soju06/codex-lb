## 1. Sweep entry point

- [x] 1.1 Add `prune_idle_http_bridge_sessions()` to the bridge session-registry mixin (mixin.py is at its architecture line ratchet): take the bridge lock, reuse `_prune_http_bridge_sessions_locked`, and schedule closes via `_schedule_http_bridge_session_closes` with reason `idle_sweep`

## 2. Heartbeat wiring

- [x] 2.1 Extract the heartbeat's bridge upkeep into `run_http_bridge_heartbeat_maintenance()` in `app/main.py` and call the sweep there beside the durable-ownership reconcile, isolating each pass so one failing cannot skip the other or stop the heartbeat

## 3. Tests

- [x] 3.1 Idle session is evicted with no request traffic; a freshly-used session is spared
- [x] 3.2 A session with pending work is spared even past its idle TTL
- [x] 3.3 Empty registry is a no-op and schedules no cleanup task
- [x] 3.4 Heartbeat maintenance runs both passes, isolates a failing one, and tolerates a missing service — so removing the wiring fails a test rather than silently restoring the leak

## 4. Spec

- [x] 4.1 Record that idle eviction does not depend on request traffic reaching the replica
