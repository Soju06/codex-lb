## 1. Persisted setting

- [x] 1.1 Add the `off` / `parent_bound_only` / `always` dashboard setting and migration; verify upgrade/downgrade and settings API tests
- [x] 1.2 Add the routing-settings control and translations; verify component and schema tests

## 2. Routing behavior

- [x] 2.1 Derive privacy-safe exact parent/child marker keys and account-neutral lineage metadata; verify affinity unit tests
- [x] 2.2 Record resolved previous-response parent evidence and prefer a non-parent account for only a fresh child, with ordinary fallback; verify focused proxy integration tests
- [x] 2.3 Prove established child and hard continuity ownership bypass diversification; verify regression tests cover both modes and fallback

## 3. Validation and delivery

- [x] 3.1 Run strict OpenSpec validation plus backend lint, type, migration, and focused/full tests
- [x] 3.2 Run frontend lint/type/tests and capture the required dashboard screenshot
- [x] 3.3 Update WORKLOG.md, commit, push the branch, open the upstream PR, and inspect current-head CI, mergeability, and review threads
