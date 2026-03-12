## 1. Runtime behavior

- [x] 1.1 Coordinate stale usage refreshes so concurrent callers share one in-flight refresh per account
- [x] 1.2 Re-check the persisted latest primary-window row before fetching usage to skip duplicate follow-on refreshes
- [x] 1.3 Sync local account objects from persisted state after shared refresh work completes

## 2. Regression coverage

- [x] 2.1 Add unit coverage proving concurrent refresh callers issue only one upstream usage fetch

## 3. Spec updates

- [x] 3.1 Document singleflight stale-usage refresh behavior in the query-caching capability
