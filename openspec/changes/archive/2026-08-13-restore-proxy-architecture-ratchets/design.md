## Context

ADR-0001 establishes `app/modules/proxy/service.py` as a stable façade and
requires CI ratchets to keep proxy implementation in focused private domains.
At commit `9b40f746`, `service.py` is 2,604 lines against a 2,600-line limit,
`load_balancer.py` is 3,260 lines against a 3,021-line limit, and
`LoadBalancer.select_account()` spans 699 lines against a 527-line limit.

The checker currently raises on the first failed assertion. PR #1416 moves one
support block out of `service.py`, but it does not touch either load-balancer
violation and therefore cannot restore a green architecture gate on its own.

`select_account()` contains three distinguishable phases: shared input loading
and continuity preflight, separate retry loops for requests with and without a
sticky key, and shared result/error construction. The sticky retry loop spans
about 285 lines, and its existing `_select_with_stickiness()` policy helper
spans about 250 lines without using `self`; together they form a cohesive
private implementation boundary. Extracting that boundary alone restores both
load-balancer ratchets with margin.

## Goals / Non-Goals

**Goals:**

- Restore every current architecture threshold without relaxing the ratchets.
- Keep `ProxyService` and `LoadBalancer.select_account()` public behavior and
  import surfaces unchanged.
- Give sticky account-selection orchestration focused private ownership with
  explicit inputs and outcomes while leaving the smaller non-sticky path in the
  public implementation module.
- Preserve lease cleanup, stale-row retries, continuity ownership, and
  settlement state across the extraction, plus the existing cache-generation
  retry on the non-sticky path.
- Report every independent architecture violation in a single checker run.

**Non-Goals:**

- Change account ranking, quota policy, routing weights, affinity semantics,
  retry counts, error codes, or operator settings.
- Raise, reset, or remove any existing line-count threshold.
- Redesign `LoadBalancer`, replace its runtime lock, or combine this work with
  unrelated proxy behavior fixes.
- Move the no-sticky retry loop when the sticky-only boundary is sufficient to
  restore both current ratchets.
- Discard PR #1416's focused façade move while replacing its branch.

## Decisions

### Use one gate-restoration change

The façade and load-balancer violations must be repaired together because every
PR runs the repository-wide architecture gate. Landing a façade-only repair
would still leave the PR red on the next fail-fast assertion. Implementation
will use a replacement branch from current `main`, port PR #1416's focused
façade-support diff, and identify the new PR as superseding #1416. Closing the
older external PR remains a maintainer action rather than an implementation
task.

Alternative considered: merge several stacked threshold fixes. Rejected because
no partial stack member can satisfy the current required check on its own.

### Extract the sticky selection path behind the public method

Create `app/modules/proxy/_load_balancer/sticky_selection.py` and move the
sticky-key retry orchestration together with the existing self-free
`_select_with_stickiness()` policy helper. `LoadBalancer.select_account()`
remains the public entry point and retains shared input loading, ownership
preflight, the no-sticky retry path, and final `AccountSelection` construction.
This private package is a load-balancer implementation boundary; it is not the
ADR-0001 `_service/account_selection/` slot, which is reserved for the
higher-level `ProxyService` budget/failover/admission layer.

The private implementation will expose a functional entry point such as
`run_sticky_selection_path(owner, *, request, selection_inputs, reload_inputs)`
against a narrow owner protocol instead of importing `LoadBalancer` back into
the module. Typed request and outcome data carriers keep the call boundary
explicit. The smallest cycle-free pure policy closure moves with the sticky
path: `_select_account_preferring_budget_safe`, both budget-threshold
predicates, `_best_health_tier_states`, `_filter_states_for_account_caps`, and
the account-cap error code/message helpers. Side effects shared with the
non-sticky path, including `_record_account_cap_rejection`, remain outside that
pure closure and reach the extracted path through an explicit typed callback.
Existing test/import surfaces are preserved through explicit re-exports from
`load_balancer.py` where required.

The extracted path must return enough state for the public method to construct
the same success or error result: selected account snapshot, acquired lease,
selection error code/message, and the current selection inputs needed for
catalog-omission metadata. It also needs an explicit terminal/direct-error
disposition because some retry-time input errors currently return before shared
opportunistic/degraded remapping. Cleanup remains owned by the path that
acquires the lease so cancellation and persistence failures cannot leak it.

Alternative considered: move the unchanged 699-line method into a mixin. This
would hide, rather than repair, the method-size violation and is rejected.

Alternative considered: extract both sticky and no-sticky retry loops. Rejected
because the sticky boundary plus its policy helper already restores both
ratchets, while moving both loops doubles the behavior-sensitive surface.

Alternative considered: split many small pure helpers while leaving the sticky
retry loop in `select_account()`. This would not create a durable ownership
boundary and would amount to shallow line shaving.

### Preserve characterization at the public boundary

Tests will continue to call `LoadBalancer.select_account()` rather than testing
private extraction helpers as the primary contract. Focused helper tests are
allowed for failure cleanup, but externally observable selection, continuity,
lease, retry, and error behavior must be characterized at the public method.

### Aggregate architecture failures deterministically

`scripts/check_proxy_architecture.py` will run independent checks in a stable
order, collect their assertion messages, print every violation, and return one
non-zero exit status. Parse failures that prevent dependent AST checks may skip
only those dependent checks; unrelated file and package checks still run.

Alternative considered: keep fail-fast behavior. Rejected because it hid two
known violations on `main` and made the repair scope emerge one CI run at a
time.

## Risks / Trade-offs

- **Selection behavior drifts during extraction** → Add public-boundary
  characterization for sticky and non-sticky success, ambiguity, hard affinity,
  cap exhaustion, stale persistence, non-sticky cache invalidation, and
  cancellation.
- **A lease survives an exception or retry** → Keep acquisition and release in
  the same private selection path and assert cleanup in failure tests.
- **Private-module imports form a cycle** → Define protocols and small data
  carriers in the private package; do not import `LoadBalancer` from it.
- **PR #1416 work is duplicated or lost** → Reconcile its two-file diff before
  implementation and record whether it was incorporated or superseded.
- **New ratchet output breaks callers that match one exact line** → Preserve the
  existing message text for each violation and the existing exit-code contract;
  only multiple messages are new.

## Migration Plan

1. Create a replacement branch from current `main`, port PR #1416's focused
   façade-support move, and record the supersession relationship without making
   closure of the older PR an implementation prerequisite.
2. Add/confirm public `select_account()` characterization tests before moving
   branch orchestration.
3. Introduce the private load-balancer package and cut over the sticky retry
   loop plus `_select_with_stickiness()` as one cohesive boundary, while keeping
   compatibility imports where current callers rely on them.
4. Change the checker to aggregate failures and add a regression fixture with
   more than one simultaneous violation.
5. Run architecture, lint, type, unit, integration, and strict OpenSpec gates.

Rollback is a code-only revert. There is no schema, configuration, or persisted
state migration.

## Open Questions

None. The replacement-branch strategy and extraction boundary are fixed for
implementation.
