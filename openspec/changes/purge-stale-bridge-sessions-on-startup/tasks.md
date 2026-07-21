## 1. Repository & Coordinator

- [x] 1.1 Add `purge_owned_sessions_on_startup` to `DurableBridgeRepository` - deletes rows owned by instance + ownerless stale rows with expired leases, plus aliases
- [x] 1.2 Add `purge_owned_sessions_on_startup` wrapper to `DurableBridgeSessionCoordinator`

## 2. Startup Integration

- [x] 2.1 Call `purge_owned_sessions_on_startup` in `lifespan` after bridge durable schema check, before serving traffic
- [x] 2.2 Gate on `bridge_durable_schema_ready is True` to avoid table-missing errors

## 3. Tests

- [x] 3.1 Add test verifying owned bridge rows are purged on startup
- [x] 3.2 Add test verifying sticky-session mappings are preserved after purge

## 4. Spec

- [x] 4.1 Create OpenSpec change `purge-stale-bridge-sessions-on-startup` with proposal, design, spec, and tasks
