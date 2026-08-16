## 1. Runtime contract

- [x] 1.1 Define and document the two pooled PostgreSQL engines per application replica in `app/db/session.py`.
- [x] 1.2 Add focused unit coverage for aggregate per-replica pool capacity.

## 2. Helm capacity policy

- [x] 2.1 Correct default and production Helm pool values and all connection-budget formulas.
- [x] 2.2 Add policy tests that calculate default and production HPA budgets from parsed values and the runtime engine count.

## 3. Verification

- [x] 3.1 Run focused unit and Helm policy tests.
- [x] 3.2 Run strict OpenSpec validation, formatting/lint checks, diff checks, and final status review.

## 4. PR-readiness remediation

- [x] 4.1 Correct the remaining main OpenSpec context formula and document the one-worker budget topology.
- [x] 4.2 Give both default and production Helm profiles a 20-connection raw reserve at their HPA ceilings.
- [x] 4.3 Bind real PostgreSQL engine creation to declared roles and pin the owned Uvicorn launcher to one worker.
- [x] 4.4 Add focused runtime, `WEB_CONCURRENCY`, Helm budget, template-plumbing, and documentation regression tests.
- [x] 4.5 Re-run focused tests and proportional static/OpenSpec verification without installing dependencies.
