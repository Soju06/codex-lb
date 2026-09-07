## 1. Implementation

- [x] 1.1 Move the "affinity may resolve a hard owner" predicate to
  `_service/support.py` (`_affinity_may_resolve_hard_owner`) and make
  `_websocket_affinity_may_resolve_hard_owner` delegate to it.
- [x] 1.2 Add `_http_bridge_accepted_replay_may_exclude_account` in
  `http_bridge/accepted_replay.py`: accepted replay + hard session key +
  hard-capable session affinity -> no exclusion; everything else unchanged.
- [x] 1.3 Consult it at the fresh-request exclusion site of
  `_retry_http_bridge_precreated_request` (model-fallback replays keep
  excluding).

## 2. Regression coverage

- [x] 2.1 Predicate unit coverage over created-only, bare session header,
  turn state, thread header with legacy process lookup, hard key without an
  owner lookup, and soft keys.
- [x] 2.2 `_retry_http_bridge_precreated_request` unit coverage on a hard
  `session_header` session whose affinity consults the raw legacy row:
  transport-close and pre-staged capacity-terminal accepted shapes reconnect
  with an empty exclusion set; the created-only shape still excludes.
- [x] 2.3 Relay-level bridge integration coverage with a two-account fake
  selection honoring `exclude_account_ids` and a raw legacy hard owner:
  capacity error, `model_at_capacity`, abrupt close 1011 and 1006 are re-sent
  to the owner within one lifecycle.
- [x] 2.4 Mutant check: deleting the guard fails the accepted unit shapes and
  all four integration terminals.

## 3. Validation

- [x] 3.1 ruff check/format, proxy architecture, cancellation safety and
  timing seam gates.
- [x] 3.2 HTTP bridge unit suite, proxy utils suite, HTTP responses bridge
  integration suite, load balancer concurrency suite.
- [x] 3.3 Strict OpenSpec validation for this change.
