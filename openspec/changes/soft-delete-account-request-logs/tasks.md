## 1. Spec

- [x] 1.1 Add request-log soft-delete dashboard requirements
- [x] 1.2 Add API-key deleted-account grouping requirements

## 2. Implementation

- [x] 2.1 Add request-log soft-delete persistence and migration
- [x] 2.2 Mark request logs when an account is deleted without changing metrics aggregates
- [x] 2.3 Hide soft-deleted rows from request-log list/filter APIs
- [x] 2.4 Group API-key account usage for deleted rows as `Deleted Accounts`

## 3. Tests

- [x] 3.1 Cover account deletion preserving and marking request logs
- [x] 3.2 Cover request-log list/facet filtering
- [x] 3.3 Cover metrics aggregates still counting soft-deleted rows
- [x] 3.4 Cover API-key account usage deleted grouping
- [x] 3.5 Validate OpenSpec changes
