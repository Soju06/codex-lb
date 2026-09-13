## 1. OpenSpec

- [x] 1.1 Add a quota-phase-planner delta for long-lived claim TTLs, scheduler
      stale-claim sweeps, and request-log-based stale-claim reconciliation.
- [x] 1.2 Sync the same contract into `openspec/specs/quota-phase-planner/spec.md`.

## 2. Implementation

- [x] 2.1 Keep warmup claim TTL at least as long as the configured Responses
      stream request budget.
- [x] 2.2 Use a decision-stable warmup request id and reconcile reclaimed claims
      from a previously committed success log before sending a new probe.
- [x] 2.3 Add a scheduler sweep for expired `executing` warmup claims that does
      not depend on the current planner output.

## 3. Validation

- [x] 3.1 Add focused regression coverage for long TTL claims, stale-claim
      scheduler sweeps, request-log-based reconciliation, and the
      `20260830_000000_add_quota_warmup_claim_expiry` migration.
- [x] 3.2 Run focused pytest, `uv run ruff check`, `uv run ruff format --check`,
      `openspec validate recover-expired-quota-warmup-claims --strict`, and
      `openspec validate --specs` so the normative specs are checked too.
