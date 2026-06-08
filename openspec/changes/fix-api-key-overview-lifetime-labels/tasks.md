# Tasks: fix-api-key-overview-lifetime-labels

- [x] Update API key overview stat labels from "7-day" to "lifetime" in
  `frontend/src/features/api-keys/components/api-keys-overview.tsx`.
- [x] Update component tests to assert the updated labels and panel titles.
- [x] Add an OpenSpec delta documenting that the dashboard overview displays
  `usageSummary` as lifetime totals and must not claim 7-day scope.
