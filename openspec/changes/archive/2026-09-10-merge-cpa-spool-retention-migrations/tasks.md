## 1. Reconcile the graph

- [x] 1.1 Reproduce the two-head failure through the migration CLI on the exact combined tree.
- [x] 1.2 Merge the inspected current main and add an explicit Alembic merge revision.
- [x] 1.3 Prove populated upgrades from each prior branch and downgrade/re-upgrade without losing retained model identities.

## 2. Verify

- [x] 2.1 Run affected migration and catalog checks, lint, type checks and strict specs.
- [x] 2.2 Independently review the exact candidate and graph boundaries.
- [x] 2.3 Assign hosted verification to this repair worker and subsequent monitoring to readiness.
