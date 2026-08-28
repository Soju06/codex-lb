## 1. Implementation

- [x] 1.1 Per-filter-signature TTL cache around `_count_recent` (bounded entries; fixed 30 s TTL application constant — made non-tunable by reduce-settings-surface-phase-2)
- [x] 1.2 Suite neutralizes the cache by patching the TTL constant to 0 via an autouse conftest fixture so in-test totals stay exact

## 2. Validation

- [x] 2.1 Statement-capture regression test: shared signature counts once across pages, distinct signature counts separately
- [x] 2.2 request-log suites green; `ruff`/`ty`; `openspec validate --specs`
