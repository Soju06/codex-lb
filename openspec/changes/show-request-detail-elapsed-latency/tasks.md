## 1. Formatter

- [ ] 1.1 Add `formatElapsed(ms)` to `frontend/src/utils/formatters.ts`
- [ ] 1.2 Add `formatElapsed` unit tests covering ms, s, and null/undefined

## 2. Dashboard UI

- [ ] 2.1 Add `Elapsed` field to the request detail dialog grid next to `Plan` in `recent-requests-table.tsx`
- [ ] 2.2 Add dialog assertion tests verifying elapsed renders for ms, s, and missing values
- [ ] 2.3 Export `formatElapsed` from the formatters import block in `recent-requests-table.tsx`
- [ ] 2.4 Confirm TypeScript compiles and all existing tests pass
