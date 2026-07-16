# Tasks

## 1. Spec

- [x] 1.1 Add `account-routing` delta requiring per-replica round-robin
  tie-break decorrelation with preserved primary ordering.
- [x] 1.2 `openspec validate replica-decorrelated-round-robin --strict` passes.

## 2. Implementation

- [x] 2.1 Add `configure_replica_salt`, salt resolution helpers, and a keyed
  `_decorrelated_tie_breaker` to `app/core/balancer/logic.py`.
- [x] 2.2 Add a `replica_salt` parameter to `select_account` and mix the
  effective salt into the final `round_robin` tie-break only.
- [x] 2.3 Export `configure_replica_salt` from `app.core.balancer` and wire it
  from the HTTP bridge instance id in `LoadBalancer.__init__`.

## 3. Tests

- [x] 3.1 Two replicas with distinct salts break an exact round-robin tie toward
  different accounts.
- [x] 3.2 Primary ordering (least-recently-selected / usage) is unchanged.
- [x] 3.3 Single-replica selection is deterministic across repeated calls.
- [x] 3.4 Salt precedence (explicit > configured > host default) is exercised.

## 4. Verification

- [x] 4.1 `uv run ruff check app tests` + `ruff format --check`.
- [x] 4.2 `uv run ty check`.
- [x] 4.3 `scripts/check_proxy_architecture.py` (load_balancer.py touched).
- [x] 4.4 Load-balancer test suites green.
