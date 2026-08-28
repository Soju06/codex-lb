# Context: restoring proxy architecture ratchets

## Purpose and scope

This change restores the architecture boundaries accepted in ADR-0001 and
makes their CI signal complete. It is an internal, behavior-preserving repair;
the normative contract is in
[`specs/proxy-architecture/spec.md`](./specs/proxy-architecture/spec.md).

## Decisions and constraints

- Existing thresholds are lower-only ratchets. Passing by increasing a number
  is outside the change.
- `ProxyService` remains the compatibility façade.
- `LoadBalancer.select_account()` remains the public selection entry point, but
  delegates the cohesive sticky retry path and its policy helper to private
  load-balancer code. The no-sticky path stays in `load_balancer.py` for now.
- Account ownership, security scope, affinity, budget, health, cap, lease,
  persistence, and catalog-omission semantics are preserved exactly.
- PR #1416 is input to a replacement branch for this combined repair; its
  focused façade diff is preserved and the eventual PR records that it
  supersedes #1416, while closure remains a maintainer action.

## Failure modes

The highest-risk failure is a structurally clean extraction that changes which
account owns a turn or fails to release a lease after cancellation. Stale-row
retries in both paths and the non-sticky cache-generation retry are also easy to
lose if only happy-path tests are retained. Public-boundary characterization
therefore precedes movement.

The checker has a different failure mode: fail-fast output makes a repair look
complete while later assertions remain red. Aggregating messages fixes the
diagnostic without changing the pass/fail contract.

## Concrete example

At the planning baseline, one run reports only:

```text
proxy architecture check failed: service.py has 2604 lines; limit is 2600
```

The same run must instead expose the façade, load-balancer file, and
`select_account()` violations together. After the extraction, it exits zero and
prints `proxy architecture checks passed`.

## Operational notes

There is no runtime rollout or data migration. Review should treat changes to
selection outcomes, error codes, or lease settlement as regressions rather than
acceptable refactor differences.
