# Design: decorrelate round-robin tie-breaking across replicas

## Problem

`select_account(..., routing_strategy="round_robin")` selects with
`min(effective_pool, key=_round_robin_sort_key)` where the key was:

```python
(_planner_cost(state, routing_costs), state.last_selected_at or 0.0, state.account_id)
```

The first two components are the intended primary ordering: prefer the lowest
planner cost, then the least-recently-selected account. The third component,
`account_id`, is a deterministic final tie-break. It is only decisive on an
*exact* tie of the first two — the common case being a cold start where every
candidate has `last_selected_at = None` (coerced to `0.0`) and no planner cost,
or after a window reset that zeroes `last_selected_at` for several accounts.

Because every replica computes the identical key, an exact tie makes all
replicas choose the same lexicographically-smallest `account_id`. Under N
replicas that is an N-way herd onto one account until its `last_selected_at`
advances and the tie breaks — by which point the next-smallest account herds,
and so on. Load does not spread across the equally-good candidates.

## Mechanism

Introduce a stable per-replica salt and mix it into the **final** tie-break
only, via a keyed hash:

```python
def _decorrelated_tie_breaker(account_id: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}\x00{account_id}".encode()).hexdigest()

# round-robin key becomes:
(_planner_cost(state, routing_costs), state.last_selected_at or 0.0,
 _decorrelated_tie_breaker(state.account_id, salt))
```

Key properties:

- **Primary ordering preserved.** Planner cost and `last_selected_at` remain the
  first two key components, so usage/health/cost ordering is byte-for-byte
  unchanged. The hash only reorders candidates that were *already exactly tied*.
- **Deterministic within a replica.** The salt is resolved once per process and
  never varies per call, so a given replica always breaks a given tie the same
  way. Single-replica behavior is therefore deterministic and unchanged in
  aggregate (a single replica still round-robins by `last_selected_at`; only the
  arbitrary tie winner may differ from lexicographic order).
- **Independent across replicas.** Distinct salts produce independent hash
  orderings, so peer replicas break the same tie toward different accounts and
  spread load across the equally-good pool.

### Salt source

Precedence in `_effective_replica_salt`:

1. An explicit `replica_salt` argument to `select_account` (used by tests and
   any caller that wants to be explicit).
2. A process-wide value set once at proxy start-up via
   `configure_replica_salt(...)`, wired from
   `settings.http_responses_session_bridge_instance_id` — the canonical
   per-replica identity already used for bridge ownership.
3. A lazily-resolved, cached default of the host identity
   (`socket.gethostname()`), matching the bridge instance-id default so an
   unconfigured process still decorrelates by pod/host.

No new configuration surface is added; the salt reuses the existing
per-replica identity.

## Rejected alternatives

- **Random per-call tie-break** (`random.shuffle` / `random.random()`): would
  spread ties but destroys determinism, making selection irreproducible and
  defeating the least-recently-selected rotation guarantees that tests and
  operators rely on. Rejected.
- **Randomize the whole sort / weighted pick like `capacity_weighted`:** changes
  the primary ordering, not just ties, and would alter single-replica behavior.
  Out of scope — round-robin must stay strictly least-recently-selected first.
- **Rank-based partitioning (replica rank modulo pool size):** requires a
  reliable global replica rank/count at selection time and reshuffles the whole
  pool on membership changes; heavier and more coupled than salting the final
  tie-break. Rejected for this focused fix.
- **Persist a shared rotation cursor in the DB:** adds write contention and a
  migration for what is a pure tie-break concern; the least-recently-selected
  timestamp already provides cross-replica rotation for the non-tie case.
  Rejected.

## SQLite vs PostgreSQL

Not applicable. The change is entirely in-memory routing logic
(`app/core/balancer/logic.py`); no schema, migration, or persisted field is
added or read. Behavior is identical on both database backends.
