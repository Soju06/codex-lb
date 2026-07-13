## 1. Persistence and API

- [x] 1.1 Add nonnegative dashboard settings columns and a single-head Alembic migration that preserves existing effective values.
- [x] 1.2 Propagate the values through settings repository, service, schemas, and `PUT /api/settings` cache invalidation/audit path.
- [x] 1.3 Expose validated capacity controls in the dashboard routing settings and preserve them in full settings updates.

## 2. Proxy runtime

- [x] 2.1 Snapshot the dashboard cache before runtime locks and use that snapshot for selection, lease acquisition, admission, and capacity errors.
- [x] 2.2 Preserve stream recovery reserve semantics without mutating global `Settings`.

## 3. Verification

- [x] 3.1 Add focused settings API/service and load-balancer regression tests, including cache-over-env behavior.
- [x] 3.2 Add focused dashboard schema, payload, and control tests.
- [x] 3.3 Run focused tests, strict OpenSpec validation, and single-head Alembic validation.
