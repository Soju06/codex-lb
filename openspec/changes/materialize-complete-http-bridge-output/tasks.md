## 1. Implementation

- [x] 1.1 Materialize ordered `response.output_item.done` items from the durable event spool.
- [x] 1.2 Persist the materialized output only after a terminal completion event is present.

## 2. Verification

- [x] 2.1 Add focused unit coverage for empty terminal output and missing completion.
- [ ] 2.2 Run focused tests, lint, and strict OpenSpec validation.
