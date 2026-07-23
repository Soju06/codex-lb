## 1. Schema and repositories

- [x] 1.1 Add one idempotent current-head revision for the focused lineage and
  compatibility schema.
- [x] 1.2 Persist monotonic security requirements on durable sessions and
  detached sticky markers.
- [x] 1.3 Preserve detached markers during cleanup and exclude them from normal
  account usage windows.

## 2. Validation

- [x] 2.1 Cover fresh, partial, and repeated migration shapes.
- [x] 2.2 Cover durable and sticky security-marker persistence.
- [x] 2.3 Run focused unit/integration tests, type checking, migration policy,
  schema drift, and strict OpenSpec validation.
- [x] 2.4 Cover cross-transport `previous_response_id` enforcement, fail-closed
  persisted requirements, and the bounded newly-classified fallback.
