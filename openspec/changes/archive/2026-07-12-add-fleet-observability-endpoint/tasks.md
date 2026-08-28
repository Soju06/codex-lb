## 1. Specification

- [x] 1.1 Add `fleet-summary` OpenSpec capability delta for fleet observability.

## 2. Backend API

- [x] 2.1 Add minimal pressure and sticky-session observability response models.
- [x] 2.2 Add read-only pressure aggregation for 30-minute and 2-hour windows.
- [x] 2.3 Add read-only sticky-session continuity aggregation.
- [x] 2.4 Add API-key-authenticated `GET /api/fleet/observability`.
- [x] 2.5 Reuse fleet summary account scoping and usage-visibility policy.

## 3. Tests

- [x] 3.1 Add integration tests for missing API key rejection.
- [x] 3.2 Add integration tests for pressure and sticky aggregation shape.
- [x] 3.3 Add integration tests for account scoping and privacy hiding.
- [x] 3.4 Add integration tests proving sensitive identifiers are omitted.
