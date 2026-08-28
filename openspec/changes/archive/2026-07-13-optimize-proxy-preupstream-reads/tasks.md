## 1. Implementation

- [x] 1.1 Raise `_api_key_cache` TTL to 60 s with the poller-invalidation rationale documented in code
- [x] 1.2 Collapse `StickySessionsRepository.upsert` to `INSERT ... ON CONFLICT ... RETURNING` (drop re-select + refresh)
- [x] 1.3 Replace the same-session `asyncio.gather` usage reads in `load_balancer._load_selection_inputs` with sequential awaits

## 2. Tests

- [x] 2.1 Statement-capture test: sticky upsert issues exactly one data statement and returns correct rows on both insert and update arms
- [x] 2.2 Cache test: mutation-driven invalidation still clears cached auth within the poller path; unchanged key served from cache without DB read
- [x] 2.3 Existing sticky/load-balancer suites green on SQLite + PostgreSQL

## 3. Validation & docs

- [x] 3.1 Update `openspec/specs/query-caching/context.md`
- [x] 3.2 `openspec validate --specs`, `ruff`, `ty`, pytest
