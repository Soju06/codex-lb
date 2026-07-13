## 1. Recoverable gate capacity wait

- [x] 1.1 Make `response_create_gate_timeout` wait-plan-eligible in the HTTP bridge capacity-wait path so non-reroutable bridged requests retry gate acquisition within the bridge request budget.
- [x] 1.2 Keep soft-affinity reroute precedence, `bridge_queue_full` fail-fast, and stuck-session retirement checks unchanged.
- [x] 1.3 Add unit coverage: gate-timeout errors produce a bounded wait plan; `capacity_exhausted_active_sessions` still does not.
- [x] 1.4 Add product-path regression: a hard-key bridged request that hits gate contention emits capacity-wait keepalives and completes after the gate frees instead of failing with 429.

## 2. Validation

- [x] 2.1 Run targeted proxy unit and integration tests.
- [x] 2.2 Validate the OpenSpec change with `openspec validate bridge-gate-capacity-wait --strict`.
