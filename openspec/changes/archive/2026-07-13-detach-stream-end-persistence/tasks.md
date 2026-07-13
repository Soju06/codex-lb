## 1. Implementation

- [x] 1.1 `_write_request_log`: detach unconditionally into `_request_log_tasks`
- [x] 1.2 `_settle_stream_api_key_usage`: detach unconditionally (transferred flag + tracking fallback)
- [x] 1.3 `ProxyService.drain_persistence_tasks`; drain in lifespan shutdown

## 2. Tests

- [x] 2.1 Hook-free contract tests: stream close precedes persistence; drain timeout reported
- [x] 2.2 Settlement unit tests updated to the detach contract (detach+close repo, detached finalize without release, failure falls back to release)
- [x] 2.3 Suite determinism: async_client response-hook drain; explicit drains in direct-service unit tests

## 3. Validation

- [x] 3.1 Full unit (3531) + integration-core + bridge/ws/e2e suites green
- [x] 3.2 `openspec validate --specs`, `ruff`, `ty`
