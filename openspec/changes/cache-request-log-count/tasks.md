## 1. Implementation

- [x] 1.1 Per-filter-signature TTL cache around `_count_recent` (bounded entries; TTL from settings; 0 disables)
- [x] 1.2 Suite disables the TTL via conftest env so in-test totals stay exact

## 2. Validation

- [x] 2.1 Statement-capture regression test: shared signature counts once across pages, distinct signature counts separately
- [x] 2.2 request-log suites green; `ruff`/`ty`; `openspec validate --specs`
