## 1. Dashboard cost-card meta copy

- [x] 1.1 Add or update dashboard view-model tests to assert that the `Est. API Cost` card meta renders only the averaged cost text for daily and weekly timeframes.
- [x] 1.2 Update `frontend/src/features/dashboard/utils.ts` so the cost-card meta reuses the existing average-cost string without appending `API estimate` or cached-token text.

## 2. Validation

- [x] 2.1 Run the targeted frontend dashboard tests.
- [x] 2.2 Validate the OpenSpec change with `openspec validate simplify-dashboard-cost-card-meta --strict`.
